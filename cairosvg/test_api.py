"""
CairoSVG API test suite.

These tests can be used as deployment tests.

"""

import io
import sys

import cairocffi as cairo
import pytest
from PIL import Image

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


def png_to_pixels(png_bytes):
    """Return a PIL image in RGBA mode from PNG bytes."""
    return Image.open(io.BytesIO(png_bytes)).convert('RGBA')


BLUR_SVG = b'''\
<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur">
      <feGaussianBlur stdDeviation="3"/>
    </filter>
  </defs>
  <rect x="25" y="25" width="50" height="50" fill="red"
        filter="url(#blur)"/>
</svg>
'''


def test_gaussian_blur():
    """A Gaussian blur softens the edges of a shape."""
    image = png_to_pixels(svg2png(BLUR_SVG))
    assert image.size == (100, 100)
    # The center of the shape is untouched
    assert image.getpixel((50, 50)) == (255, 0, 0, 255)
    # The edge of the shape is half-transparent
    red, green, blue, alpha = image.getpixel((25, 50))
    assert 100 < alpha < 180
    # The blur leaks outside of the original shape…
    assert 0 < image.getpixel((22, 50))[3] < 255
    # …but not outside of the filter region (110% of the bounding box)
    assert image.getpixel((10, 50))[3] == 0
    assert image.getpixel((0, 0))[3] == 0


def test_gaussian_blur_transparent_edges():
    """Blurred transparent edges keep their color, without dark fringes."""
    image = png_to_pixels(svg2png(BLUR_SVG))
    for position in ((25, 50), (22, 50), (50, 25), (75, 50)):
        red, green, blue, alpha = image.getpixel(position)
        assert 0 < alpha < 255
        assert red == 255
        assert green == 0
        assert blue == 0


def test_gaussian_blur_anisotropic():
    """stdDeviation controls horizontal and vertical blur separately."""
    image = png_to_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur">
      <feGaussianBlur stdDeviation="4 0"/>
    </filter>
  </defs>
  <rect x="25" y="25" width="50" height="50" fill="lime"
        filter="url(#blur)"/>
</svg>'''))
    # Horizontal blur only: soft left/right edges, sharp top/bottom edges
    assert 0 < image.getpixel((22, 50))[3] < 255
    assert image.getpixel((50, 24))[3] == 0
    assert image.getpixel((50, 26))[3] == 255

    # Same test with vertical blur only
    image = png_to_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur">
      <feGaussianBlur stdDeviation="0 4"/>
    </filter>
  </defs>
  <rect x="25" y="25" width="50" height="50" fill="lime"
        filter="url(#blur)"/>
</svg>'''))
    assert 0 < image.getpixel((50, 22))[3] < 255
    assert image.getpixel((24, 50))[3] == 0
    assert image.getpixel((26, 50))[3] == 255


def test_gaussian_blur_group_opacity():
    """A group is blurred as a whole, opacity is only applied once."""
    image = png_to_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur">
      <feGaussianBlur stdDeviation="2"/>
    </filter>
  </defs>
  <g filter="url(#blur)" opacity="0.5">
    <rect x="20" y="20" width="20" height="20" fill="red"/>
    <rect x="60" y="60" width="20" height="20" fill="blue"/>
  </g>
</svg>'''))
    # Both children are blurred, with 0.5 opacity applied once
    assert image.getpixel((30, 30)) == (255, 0, 0, 128)
    assert image.getpixel((70, 70)) == (0, 0, 255, 128)
    assert 0 < image.getpixel((19, 30))[3] < 128


def test_gaussian_blur_shared_filter():
    """Objects sharing the same filter are blurred independently."""
    image = png_to_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur">
      <feGaussianBlur stdDeviation="2"/>
    </filter>
  </defs>
  <rect x="10" y="10" width="20" height="20" fill="red"
        filter="url(#blur)"/>
  <rect x="60" y="60" width="20" height="20" fill="red"
        filter="url(#blur)"/>
</svg>'''))
    assert image.getpixel((20, 20)) == (255, 0, 0, 255)
    assert image.getpixel((70, 70)) == (255, 0, 0, 255)
    # Corresponding edge pixels of both shapes get the same blur
    assert image.getpixel((10, 20)) == image.getpixel((60, 70))
    assert 0 < image.getpixel((10, 20))[3] < 255


def test_gaussian_blur_filter_region():
    """The blur is clipped to the filter region."""
    image = png_to_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur" filterUnits="userSpaceOnUse"
            x="0" y="0" width="60" height="60">
      <feGaussianBlur stdDeviation="5"/>
    </filter>
  </defs>
  <rect x="25" y="25" width="50" height="50" fill="red"
        filter="url(#blur)"/>
</svg>'''))
    assert image.getpixel((55, 50))[3] > 0
    assert image.getpixel((61, 50))[3] == 0
    assert image.getpixel((70, 70))[3] == 0


def test_gaussian_blur_primitive_units():
    """stdDeviation can be expressed in object bounding box units."""
    image = png_to_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur" primitiveUnits="objectBoundingBox">
      <feGaussianBlur stdDeviation="0.06"/>
    </filter>
  </defs>
  <rect x="25" y="25" width="50" height="50" fill="red"
        filter="url(#blur)"/>
</svg>'''))
    # 0.06 * 50 = 3 user units: same result as stdDeviation="3"
    reference = png_to_pixels(svg2png(BLUR_SVG))
    assert image.getpixel((25, 50)) == reference.getpixel((25, 50))
    assert image.getpixel((22, 50)) == reference.getpixel((22, 50))


def test_gaussian_blur_scale():
    """The blur radius follows the output scale."""
    image_1 = png_to_pixels(svg2png(BLUR_SVG))
    image_2 = png_to_pixels(svg2png(BLUR_SVG, scale=2))
    assert image_1.size == (100, 100)
    assert image_2.size == (200, 200)
    # A pixel 3 units away from the edge gets the same blur at both scales
    alpha_1 = image_1.getpixel((22, 50))[3]
    alpha_2 = image_2.getpixel((44, 100))[3]
    assert abs(alpha_1 - alpha_2) < 20
    assert image_2.getpixel((100, 100)) == (255, 0, 0, 255)


def test_gaussian_blur_transform():
    """The blur radius follows the object transform."""
    image = png_to_pixels(svg2png(b'''\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <defs>
    <filter id="blur">
      <feGaussianBlur stdDeviation="2"/>
    </filter>
  </defs>
  <rect x="10" y="10" width="20" height="20" fill="red"
        filter="url(#blur)" transform="translate(30 30) scale(2)"/>
</svg>'''))
    assert image.getpixel((70, 70)) == (255, 0, 0, 255)
    # The edge is at 30 + 10 * 2 = 50
    assert 100 < image.getpixel((50, 70))[3] < 180


def test_gaussian_blur_invalid():
    """Invalid blur parameters raise an error."""
    template = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
  <defs>
    <filter id="blur">
      <feGaussianBlur {attributes}/>
    </filter>
  </defs>
  <rect width="10" height="10" filter="url(#blur)"/>
</svg>'''
    for attributes in (
            'stdDeviation="abc"', 'stdDeviation="-2"', 'stdDeviation="1 2 3"',
            'in="BackgroundImage" stdDeviation="2"'):
        with pytest.raises(ValueError):
            svg2png(template.format(attributes=attributes).encode())


def test_gaussian_blur_no_filter_regression():
    """Images without blur filters are left untouched."""
    assert svg2png(SVG_SAMPLE).startswith(MAGIC_NUMBERS['PNG'])
    image = png_to_pixels(svg2png(SVG_SAMPLE))
    # The rectangle keeps sharp edges
    assert image.getpixel((6, 11))[3] == 255
    assert image.getpixel((2, 11))[3] == 0
