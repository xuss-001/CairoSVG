"""
CairoSVG API test suite.

These tests can be used as deployment tests.

"""

import io
import sys

import cairocffi as cairo
import pytest

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


def png_pixels(png_bytes):
    """Return ``(width, height, pixel)`` for a PNG byte string.

    ``pixel(x, y)`` returns the premultiplied ``(r, g, b, a)`` channels of
    the pixel at ``(x, y)``.

    """
    image = cairo.ImageSurface.create_from_png(io.BytesIO(png_bytes))
    data = image.get_data()
    stride = image.get_stride()

    def pixel(x, y):
        offset = y * stride + x * 4
        blue, green, red, alpha = data[offset:offset + 4]
        return red, green, blue, alpha

    # Keep the image and its pixel buffer alive
    pixel.image, pixel.data = image, data
    return image.get_width(), image.get_height(), pixel


BLUR_SVG = b'''<svg xmlns="http://www.w3.org/2000/svg"
    width="100" height="100">
  <defs><filter id="blur"><feGaussianBlur stdDeviation="4"/></filter></defs>
  <rect x="30" y="30" width="40" height="40" fill="red"
        filter="url(#blur)"/>
</svg>'''


def test_filter_gaussian_blur():
    """Blur a shape, check colors and transparency on the blurred edges."""
    width, height, pixel = png_pixels(svg2png(BLUR_SVG))
    assert (width, height) == (100, 100)
    # The center of the rectangle is untouched
    assert pixel(50, 50) == (255, 0, 0, 255)
    # The blur extends beyond the rectangle's edges…
    red, green, blue, alpha = pixel(26, 50)
    assert 0 < alpha < 255
    # …with no black fringe: red stays proportional to alpha
    assert abs(red - alpha) <= 2
    assert green == blue == 0
    # Pixels outside of the filter region stay transparent
    assert pixel(5, 5) == (0, 0, 0, 0)
    assert pixel(25, 50)[3] == 0


def test_filter_gaussian_blur_anisotropic():
    """Blur horizontally or vertically only with two stdDeviation values."""
    svg = BLUR_SVG.replace(b'stdDeviation="4"', b'stdDeviation="6 0"')
    _, _, pixel = png_pixels(svg2png(svg))
    # Horizontal blur, no vertical blur
    assert pixel(27, 50)[3] > 0
    assert pixel(50, 27)[3] == 0

    svg = BLUR_SVG.replace(b'stdDeviation="4"', b'stdDeviation="0 6"')
    _, _, pixel = png_pixels(svg2png(svg))
    # Vertical blur, no horizontal blur
    assert pixel(27, 50)[3] == 0
    assert pixel(50, 27)[3] > 0


def test_filter_gaussian_blur_group():
    """Blur a whole group of shapes."""
    _, _, pixel = png_pixels(svg2png(b'''
        <svg xmlns="http://www.w3.org/2000/svg"
        width="100" height="100">
      <defs>
        <filter id="blur"><feGaussianBlur stdDeviation="3"/></filter>
      </defs>
      <g filter="url(#blur)">
        <rect x="20" y="40" width="25" height="20" fill="black"/>
        <rect x="55" y="40" width="25" height="20" fill="black"/>
      </g>
    </svg>'''))
    # The blur of both rectangles leaks into the gap between them
    assert pixel(50, 50)[3] > 0
    # The blur extends beyond the rectangles, within the filter region
    assert pixel(30, 39)[3] > 0
    # The filter region (120% of the group's bounding box) clips the blur
    assert pixel(30, 36)[3] == 0


def test_filter_gaussian_blur_filter_units():
    """Clip the blur to a filter region in userSpaceOnUse units."""
    _, _, pixel = png_pixels(svg2png(b'''
        <svg xmlns="http://www.w3.org/2000/svg"
        width="100" height="100">
      <defs>
        <filter id="blur" filterUnits="userSpaceOnUse"
                x="30" y="30" width="45" height="45">
          <feGaussianBlur stdDeviation="8"/>
        </filter>
      </defs>
      <rect x="30" y="30" width="40" height="40" fill="red"
            filter="url(#blur)"/>
    </svg>'''))
    # The blur is visible inside the filter region…
    assert pixel(73, 50)[3] > 0
    # …but is clipped by the region even though it would extend further
    assert pixel(78, 50)[3] == 0
    assert pixel(28, 50)[3] == 0


def test_filter_gaussian_blur_primitive_units():
    """Use objectBoundingBox primitive units for stdDeviation."""
    _, _, pixel = png_pixels(svg2png(b'''
        <svg xmlns="http://www.w3.org/2000/svg"
        width="100" height="100">
      <defs>
        <filter id="blur" primitiveUnits="objectBoundingBox">
          <feGaussianBlur stdDeviation="0.1"/>
        </filter>
      </defs>
      <rect x="30" y="30" width="40" height="40" fill="red"
            filter="url(#blur)"/>
    </svg>'''))
    # 0.1 of the 40px-wide bounding box gives the same blur as BLUR_SVG
    _, _, reference = png_pixels(svg2png(BLUR_SVG))
    assert abs(pixel(26, 50)[3] - reference(26, 50)[3]) <= 2
    assert pixel(50, 50) == (255, 0, 0, 255)


def test_filter_gaussian_blur_output_size():
    """Scale the blur with the output size."""
    width, height, pixel = png_pixels(
        svg2png(BLUR_SVG, output_width=200, output_height=200))
    assert (width, height) == (200, 200)
    _, _, reference = png_pixels(svg2png(BLUR_SVG))
    # One standard deviation away from the edge gives the same alpha at
    # both scales
    assert abs(pixel(52, 100)[3] - reference(26, 50)[3]) <= 10
    assert pixel(100, 100) == (255, 0, 0, 255)


def test_filter_gaussian_blur_transform():
    """Apply the blur to a transformed object."""
    _, _, pixel = png_pixels(svg2png(
        BLUR_SVG.replace(b'<rect ', b'<rect transform="translate(10 0)" ')))
    # The blurred rectangle is translated by 10 pixels
    assert pixel(60, 50) == (255, 0, 0, 255)
    assert 0 < pixel(36, 50)[3] < 255
    assert pixel(25, 50)[3] == 0


def test_filter_gaussian_blur_opacity():
    """Apply the node's opacity only once when blurring."""
    _, _, pixel = png_pixels(svg2png(
        BLUR_SVG.replace(b'<rect ', b'<rect opacity="0.5" ')))
    red, _, _, alpha = pixel(50, 50)
    assert abs(alpha - 128) <= 4
    assert abs(red - alpha) <= 2


def test_filter_gaussian_blur_shared():
    """Share a filter between objects without interference."""
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
      <defs>
        <filter id="blur"><feGaussianBlur stdDeviation="4"/></filter>
      </defs>
      <rect x="20" y="30" width="40" height="40" fill="red"
            filter="url(#blur)"/>
      <rect x="120" y="30" width="40" height="40" fill="red"
            filter="url(#blur)"/>
    </svg>'''
    _, _, pixel = png_pixels(svg2png(svg))
    # Both rectangles are blurred…
    assert 0 < pixel(17, 50)[3] < 255
    assert 0 < pixel(117, 50)[3] < 255
    # …and the blur of one doesn't leak onto the other
    assert pixel(90, 50)[3] == 0
    # Rendering is deterministic
    assert svg2png(svg) == svg2png(svg)


def test_filter_gaussian_blur_invalid():
    """Raise an error on invalid stdDeviation values."""
    for value in ('-1', 'foo', '1 2 3'):
        with pytest.raises(ValueError):
            svg2png(BLUR_SVG.replace(
                b'stdDeviation="4"', f'stdDeviation="{value}"'.encode()))


def test_filter_offset():
    """Check that existing filters are left unchanged."""
    _, _, pixel = png_pixels(svg2png(b'''
        <svg xmlns="http://www.w3.org/2000/svg"
        width="100" height="100">
      <defs><filter id="offset"><feOffset dx="10"/></filter></defs>
      <rect x="30" y="30" width="20" height="20" fill="red"
            filter="url(#offset)"/>
    </svg>'''))
    assert pixel(45, 40) == (255, 0, 0, 255)
    assert pixel(35, 40)[3] == 0
