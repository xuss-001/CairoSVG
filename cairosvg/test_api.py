"""
CairoSVG API test suite.

These tests can be used as deployment tests.

"""

import io
import math
import re
import sys

import cairocffi as cairo
import pytest
from PIL import Image, ImageChops

from . import SURFACES, VERSION, parser, surface, svg2pdf, svg2png
from .__main__ import main

MAGIC_NUMBERS = {
    'SVG': b'<?xml',
    'PNG': b'\211PNG\r\n\032\n',
    'PDF': b'%PDF',
    'PS': b'%!',
}

SVG_SAMPLE = b'''\
<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="4in" height="5in">
  <rect x="5" y="10" width="13" height="15"
        fill="lime" stroke="black" stroke-width="1" />
</svg>
'''


@pytest.mark.parametrize('format_name', MAGIC_NUMBERS)
def test_formats(format_name):
    """Convert to a given format and test that output looks right."""
    content = SURFACES[format_name].convert(SVG_SAMPLE)
    assert content.startswith(MAGIC_NUMBERS[format_name])


def read_file(filename):
    """Shortcut to return the whole content of a file as a byte string."""
    with open(filename, 'rb') as file_object:
        return file_object.read()


def render_png(svg, **kwargs):
    """Render an SVG string to a Pillow RGB image."""
    content = svg2png(bytestring=svg.encode(), **kwargs)
    return Image.open(io.BytesIO(content)).convert('RGB')


def transform_matrix(value):
    """Return the Cairo matrix matching an SVG transform attribute."""
    matrix = cairo.Matrix()
    for name, arguments in re.findall(r'(\w+)\s*\(([^)]*)\)', value):
        values = [
            float(number) for number in re.split(r'[\s,]+', arguments.strip())
            if number]
        operation = cairo.Matrix()
        if name == 'translate':
            operation.translate(*values)
        elif name == 'rotate':
            operation.rotate(math.radians(values[0]))
        elif name == 'scale':
            operation.scale(*(values if len(values) > 1 else values * 2))
        elif name == 'skewX':
            operation = cairo.Matrix(
                1, 0, math.tan(math.radians(values[0])), 1, 0, 0)
        elif name == 'skewY':
            operation = cairo.Matrix(
                1, math.tan(math.radians(values[0])), 0, 1, 0, 0)
        matrix = operation.multiply(matrix)
    return matrix


def transformed_points(points, matrix):
    """Serialize transformed points for an SVG ``points`` attribute."""
    return ' '.join(
        f'{x:.9f},{y:.9f}'
        for x, y in (matrix.transform_point(*point) for point in points))


def sampled_gray(image, x, y):
    """Return a bilinearly sampled grayscale value from a Pillow image."""
    if not 0 <= x < image.width - 1 or not 0 <= y < image.height - 1:
        return 255
    x0, y0 = int(x), int(y)
    x1, y1 = x0 + 1, y0 + 1
    x_ratio, y_ratio = x - x0, y - y0
    top = (
        image.getpixel((x0, y0)) * (1 - x_ratio) +
        image.getpixel((x1, y0)) * x_ratio)
    bottom = (
        image.getpixel((x0, y1)) * (1 - x_ratio) +
        image.getpixel((x1, y1)) * x_ratio)
    return top * (1 - y_ratio) + bottom * y_ratio


def stroke_width(image, start, end):
    """Measure stroke width perpendicular to a line in a grayscale image."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    normal = -dy / length, dx / length
    middle = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
    step = 0.05
    coverage = 0
    for offset in range(-200, 201):
        distance = offset * step
        gray = sampled_gray(
            image, middle[0] + normal[0] * distance,
            middle[1] + normal[1] * distance)
        coverage += (1 - gray / 255) * step
    return coverage


def test_api(tmp_path):
    """Test the Python API with various parameters."""
    expected_content = svg2png(SVG_SAMPLE)
    # Already tested above: just a sanity check:
    assert expected_content.startswith(MAGIC_NUMBERS['PNG'])

    # Read from a byte string
    assert svg2png(SVG_SAMPLE) == expected_content
    assert svg2png(bytestring=SVG_SAMPLE) == expected_content

    file_like = io.BytesIO(SVG_SAMPLE)
    # Read from a file-like object
    assert svg2png(file_obj=file_like) == expected_content

    file_like = io.BytesIO()
    # Write to a file-like object
    svg2png(SVG_SAMPLE, write_to=file_like)
    assert file_like.getvalue() == expected_content

    temp_0 = tmp_path / 'sample_0.svg'
    temp_0.write_bytes(SVG_SAMPLE)

    # Read from a filename
    assert svg2png(url=str(temp_0)) == expected_content
    assert svg2png(url=f'file://{temp_0}') == expected_content

    # Read from a pathlib.Path
    assert svg2png(url=temp_0) == expected_content

    with temp_0.open('rb') as file_object:
        # Read from a real file object
        assert svg2png(file_obj=file_object) == expected_content

    temp_1 = tmp_path / 'result_1.png'
    with temp_1.open('wb') as file_object:
        # Write to a real file object
        svg2png(SVG_SAMPLE, write_to=file_object)
    assert read_file(str(temp_1)) == expected_content

    temp_2 = tmp_path / 'result_2.png'
    # Write to a filename
    svg2png(SVG_SAMPLE, write_to=str(temp_2))
    assert read_file(str(temp_2)) == expected_content

    file_like = io.BytesIO()
    try:
        svg2png(write_to=file_like)
    except TypeError:
        pass
    else:  # pragma: no cover
        raise Exception('TypeError not raised')


@pytest.mark.parametrize('transform', (
    'translate(80 70) rotate(22) scale(2.5 0.7) skewX(15)',
    'translate(100 55) rotate(-35) scale(0.8 3) skewY(-12)',
    'translate(60 100) rotate(40) scale(2.2 0.9) skewX(-20)',
))
def test_non_scaling_stroke_width_under_affine_transforms(transform):
    """Stroke width is constant under non-uniform scaling and skew."""
    svg = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="320" height="260">
  <rect width="320" height="260" fill="white"/>
  <g transform="{transform}">
    <line x1="10" y1="20" x2="70" y2="20"
          stroke="black" stroke-width="8" stroke-linecap="butt"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>
'''
    image = render_png(svg).convert('L')
    matrix = transform_matrix(transform)
    start = matrix.transform_point(10, 20)
    end = matrix.transform_point(70, 20)
    assert 7.5 <= stroke_width(image, start, end) <= 8.5


def test_stroke_width_still_scales_without_vector_effect():
    """Elements without the attribute keep the original stroke behavior."""
    transform = 'translate(20 20) scale(3 1)'
    svg = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="160" height="120">
  <rect width="160" height="120" fill="white"/>
  <g transform="{transform}">
    <line x1="20" y1="10" x2="20" y2="60"
          stroke="black" stroke-width="6" stroke-linecap="butt"/>
  </g>
</svg>
'''
    image = render_png(svg).convert('L')
    matrix = transform_matrix(transform)
    start = matrix.transform_point(20, 10)
    end = matrix.transform_point(20, 60)
    assert 17.5 <= stroke_width(image, start, end) <= 18.5


@pytest.mark.parametrize('tag, element', (
    ('path', '<path d="M 10 10 L 60 10"/>'),
    ('line', '<line x1="10" y1="10" x2="60" y2="10"/>'),
    ('polyline', '<polyline points="10,10 60,10"/>'),
    ('polygon', '<polygon points="10,10 60,10 60,40 10,40"/>'),
))
def test_non_scaling_stroke_supported_elements(tag, element):
    """All supported path elements honor a CSS vector-effect value."""
    element = element.replace(
        '/>', ' fill="none" stroke="black" stroke-width="6" '
        'style="vector-effect: non-scaling-stroke"/>')
    svg = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80">
  <rect width="200" height="80" fill="white"/>
  <g transform="translate(10 20) scale(3 1)">{element}</g>
</svg>
'''
    image = render_png(svg).convert('L')
    matrix = transform_matrix('translate(10 20) scale(3 1)')
    start = matrix.transform_point(10, 10)
    end = matrix.transform_point(60, 10)
    assert 5.5 <= stroke_width(image, start, end) <= 6.5, tag


def test_non_scaling_stroke_with_non_uniform_viewport():
    """A non-uniform root viewport keeps both stroke directions equal."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="300" height="100"
     viewBox="0 0 100 100" preserveAspectRatio="none">
  <rect width="100" height="100" fill="white"/>
  <line x1="20" y1="15" x2="80" y2="15"
        stroke="black" stroke-width="5"
        vector-effect="non-scaling-stroke"/>
  <line x1="15" y1="30" x2="15" y2="85"
        stroke="black" stroke-width="5"
        vector-effect="non-scaling-stroke"/>
</svg>
'''
    image = render_png(svg).convert('L')
    horizontal = stroke_width(image, (60, 15), (240, 15))
    vertical = stroke_width(image, (45, 30), (45, 85))
    assert 8.2 <= horizontal <= 9.1
    assert 8.2 <= vertical <= 9.1


def test_non_scaling_stroke_dashes_caps_and_joins():
    """Dash, cap and join geometry uses unscaled SVG coordinates."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="260" height="180">
  <rect width="260" height="180" fill="white"/>
  <g transform="translate(20 20) scale(3 1)">
    <line x1="10" y1="10" x2="70" y2="10"
          stroke="black" stroke-width="6"
          stroke-dasharray="6 6" stroke-linecap="butt"
          vector-effect="non-scaling-stroke"/>
  </g>
  <g transform="translate(20 80) scale(3 1)">
    <path d="M 10 10 L 40 10 L 40 30" fill="none"
          stroke="black" stroke-width="10"
          stroke-linecap="round" stroke-linejoin="round"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>
'''
    image = render_png(svg)
    pixels = image.load()

    runs = []
    start = None
    for x in range(20, 240):
        dark = pixels[x, 30][0] < 128
        if dark and start is None:
            start = x
        elif not dark and start is not None:
            runs.append(x - start)
            start = None
    # The 6/6 dash pattern remains 6 pixels in device space.
    assert runs[:4] == [6, 6, 6, 6]

    # Round cap: half of the 10-pixel stroke width extends past the endpoint.
    assert pixels[47, 90][0] < 128
    assert pixels[44, 90][0] > 200

    # Round join: the outside corner is filled within a 5-pixel radius.
    assert pixels[143, 87][0] < 128
    assert pixels[147, 83][0] > 200


def test_non_scaling_stroke_fill_clip_and_opacity():
    """Fill, clipping and opacity match an equivalent device-space path."""
    transform = (
        'translate(180 120) rotate(17) scale(2.1 0.75) skewX(15)')
    matrix = transform_matrix(transform)
    shape = [(-45, -30), (40, -30), (40, 30), (-45, 30)]
    clip = [(-43, -28), (25, -28), (25, 15), (-43, 15)]
    style = (
        'fill="rgb(30,120,200)" fill-opacity="0.65" '
        'stroke="rgb(10,25,55)" stroke-width="5" '
        'stroke-dasharray="9 6" stroke-dashoffset="2" '
        'stroke-linecap="round" stroke-linejoin="round" opacity="0.75"')
    identity = cairo.Matrix()
    source = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="360" height="240">
  <defs>
    <clipPath id="clip">
      <polygon points="{transformed_points(clip, identity)}"/>
    </clipPath>
  </defs>
  <rect width="360" height="240" fill="white"/>
  <g transform="{transform}">
    <polygon points="{transformed_points(shape, identity)}" {style}
             clip-path="url(#clip)" vector-effect="non-scaling-stroke"/>
  </g>
</svg>
'''
    reference = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="360" height="240">
  <defs>
    <clipPath id="clip">
      <polygon points="{transformed_points(clip, matrix)}"/>
    </clipPath>
  </defs>
  <rect width="360" height="240" fill="white"/>
  <polygon points="{transformed_points(shape, matrix)}" {style}
           clip-path="url(#clip)"/>
</svg>
'''
    image = render_png(source)
    expected = render_png(reference)
    assert ImageChops.difference(image, expected).getbbox() is None

    # The transformed fill is visible, but clipped areas and transformed
    # strokes outside of them stay untouched.
    fill_x, fill_y = matrix.transform_point(-20, 0)
    clip_x, clip_y = matrix.transform_point(30, 0)
    assert image.getpixel((round(fill_x), round(fill_y)))[2] > 200
    assert image.getpixel((round(clip_x), round(clip_y))) == (255, 255, 255)


def test_non_scaling_stroke_follows_output_scale():
    """PNG output scaling enlarges both geometry and non-scaling strokes."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100">
  <rect width="160" height="100" fill="white"/>
  <g transform="translate(10 30) scale(2 1)">
    <line x1="10" y1="10" x2="60" y2="10"
          stroke="black" stroke-width="8" stroke-linecap="butt"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>
'''
    image = render_png(svg, scale=2).convert('L')
    start = 2 * 30, 2 * 40
    end = 2 * 130, 2 * 40
    assert 15.5 <= stroke_width(image, start, end) <= 16.5


def test_low_level_api():
    """Test the low-level Python API with various parameters."""
    expected_content = svg2png(SVG_SAMPLE)

    # Same as above, longer version
    tree = parser.Tree(bytestring=SVG_SAMPLE)
    file_like = io.BytesIO()
    png_surface = surface.PNGSurface(tree, file_like, 96)
    png_surface.finish()
    assert file_like.getvalue() == expected_content

    png_result = cairo.ImageSurface.create_from_png(
        io.BytesIO(expected_content))
    expected_width = png_result.get_width()
    expected_height = png_result.get_height()

    # Abstract surface
    png_surface = surface.PNGSurface(tree, None, 96)
    assert png_surface.width == expected_width
    assert png_surface.height == expected_height
    assert cairo.SurfacePattern(png_surface.cairo)


def test_script(tmp_path):
    """Test the ``cairosvg`` script and the ``main`` function."""
    expected_png = svg2png(SVG_SAMPLE)[:100]
    expected_pdf = svg2pdf(SVG_SAMPLE)[:100]

    def test_main(args, exit_=False, input_=None, full=False):
        """Test main called with given ``args``.

        If ``exit_`` is ``True``, check that ``SystemExit`` is raised. We then
        assume that the program output is an unicode string.

        If ``input_`` is given, use this stream as input stream.

        """
        sys.argv = ['cairosvg'] + args
        old_stdin, old_stdout = sys.stdin, sys.stdout

        output_buffer = io.BytesIO()
        sys.stdout = io.TextIOWrapper(output_buffer)

        if input_:
            sys.stdin = open(input_, 'rb')
            sys.stdin.buffer = sys.stdin

        if exit_:
            try:
                main()
            except SystemExit:
                pass
            else:  # pragma: no cover
                raise Exception('CairoSVG did not exit')
        else:
            main()

        sys.stdout.flush()
        output = output_buffer.getvalue()
        sys.stdin, sys.stdout = old_stdin, old_stdout
        return output if full else output[:100]

    svg_path = tmp_path / "test.svg"
    svg_path.write_bytes(SVG_SAMPLE)
    svg_filename = str(svg_path)

    assert test_main(['--help'], exit_=True).startswith(b'usage: ')
    assert test_main(['--version'], exit_=True).strip() == (
        VERSION.encode('ascii'))

    assert test_main([svg_filename]) == expected_pdf
    assert test_main([svg_filename, '-d', '96', '-f', 'pdf']) == expected_pdf
    assert test_main([svg_filename, '-f', 'png']) == expected_png
    assert test_main(['-'], input_=svg_filename) == expected_pdf

    # Test DPI
    output = test_main([svg_filename, '-d', '10', '-f', 'png'], full=True)
    image = cairo.ImageSurface.create_from_png(io.BytesIO(output))
    assert image.get_width() == 40
    assert image.get_height() == 50

    temp_1 = tmp_path / 'result_1'
    # Default to PDF
    assert not test_main([svg_filename, '-o', str(temp_1)])
    assert read_file(temp_1)[:100] == expected_pdf

    temp_2 = tmp_path / 'result_2.png'
    # Guess from the file extension
    assert not test_main([svg_filename, '-o', str(temp_2)])
    assert read_file(temp_2)[:100] == expected_png

    temp_3 = tmp_path / 'result_3.png'
    # Explicit -f wins
    assert not test_main([svg_filename, '-o', str(temp_3), '-f', 'pdf'])
    assert read_file(str(temp_3))[:100] == expected_pdf
