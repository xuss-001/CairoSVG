"""
CairoSVG API test suite.

These tests can be used as deployment tests.

"""

import io
import sys

import cairocffi as cairo
import pytest

from . import SURFACES, VERSION, parser, surface, svg2pdf, svg2png, svg2svg
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


def clip_pixel_getter(png_bytes):
    """Return a function giving the ``(r, g, b, a)`` of a PNG pixel."""
    image = cairo.ImageSurface.create_from_png(io.BytesIO(png_bytes))
    data = image.get_data()
    stride = image.get_stride()

    def get_pixel(x, y):
        offset = y * stride + x * 4
        blue, green, red, alpha = data[offset:offset + 4]
        return red, green, blue, alpha

    return get_pixel


TRANSPARENT = (0, 0, 0, 0)
BLUE = (0, 0, 255, 255)
RED = (255, 0, 0, 255)


def test_clip_path_evenodd_hole():
    """A child with clip-rule="evenodd" punches holes in the clip."""
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="clip">
              <path clip-rule="evenodd" fill="red"
                    d="M10 10 h80 v80 h-80 z M30 30 h40 v40 h-40 z"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue" clip-path="url(#clip)"/>
        </svg>'''))
    assert pixel(20, 20) == BLUE
    assert pixel(50, 50) == TRANSPARENT  # the hole is not filled
    assert pixel(5, 5) == TRANSPARENT


def test_clip_path_union_and_mixed_rules():
    """Children combine as a union, each with its own clip-rule."""
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="clip">
              <path clip-rule="evenodd"
                    d="M10 10 h40 v40 h-40 z M20 20 h20 v20 h-20 z"/>
              <rect x="40" y="40" width="30" height="30"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue" clip-path="url(#clip)"/>
        </svg>'''))
    assert pixel(15, 15) == BLUE       # ring of the evenodd path
    assert pixel(30, 30) == TRANSPARENT  # hole of the evenodd path
    assert pixel(60, 60) == BLUE       # second child
    assert pixel(45, 45) == BLUE       # overlap of both children
    assert pixel(80, 80) == TRANSPARENT


def test_clip_path_inherited_clip_rule():
    """clip-rule is inherited from the clipPath element to its children."""
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="clip" clip-rule="evenodd">
              <path d="M10 10 h80 v80 h-80 z M30 30 h40 v40 h-40 z"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue" clip-path="url(#clip)"/>
        </svg>'''))
    assert pixel(20, 20) == BLUE
    assert pixel(50, 50) == TRANSPARENT


def test_clip_path_nested():
    """A child's own clip-path intersects it before the union, and the
    clipPath's own clip-path intersects the whole result."""
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="left">
              <rect x="0" y="0" width="50" height="100"/>
            </clipPath>
            <clipPath id="bottom">
              <rect x="0" y="50" width="100" height="50"/>
            </clipPath>
            <clipPath id="combined" clip-path="url(#bottom)">
              <rect x="25" y="0" width="50" height="100"
                    clip-path="url(#left)"/>
              <rect x="80" y="0" width="20" height="100"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue"
                clip-path="url(#combined)"/>
        </svg>'''))
    # First child intersected with its own clip: x in [25, 50]
    assert pixel(40, 75) == BLUE
    assert pixel(60, 75) == TRANSPARENT
    # Second child of the union is not affected by the first child's clip
    assert pixel(90, 75) == BLUE
    # The clipPath's own clip-path intersects the whole union
    assert pixel(40, 25) == TRANSPARENT
    assert pixel(90, 25) == TRANSPARENT


def test_clip_path_object_bounding_box():
    """objectBoundingBox uses the real geometric bounding box of the
    target, including non-zero origins, curves and groups."""
    # Curved path without width/height attributes, non-zero origin
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="clip" clipPathUnits="objectBoundingBox">
              <rect x="0.5" y="0" width="0.5" height="1"/>
            </clipPath>
          </defs>
          <path d="M 20 20 C 20 10 80 10 80 20 L 80 80 L 20 80 Z"
                fill="blue" clip-path="url(#clip)"/>
        </svg>'''))
    assert pixel(30, 50) == TRANSPARENT
    assert pixel(70, 50) == BLUE

    # Group target: bounding box is the union of the children boxes
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="clip" clipPathUnits="objectBoundingBox">
              <rect x="0" y="0" width="0.5" height="1"/>
            </clipPath>
          </defs>
          <g clip-path="url(#clip)">
            <rect x="10" y="10" width="30" height="80" fill="blue"/>
            <rect x="60" y="10" width="30" height="80" fill="red"/>
          </g>
        </svg>'''))
    # Group bounding box is x in [10, 90]: left half ends at x = 50
    assert pixel(20, 50) == BLUE
    assert pixel(70, 50) == TRANSPARENT


def test_clip_path_reuse_and_order():
    """The same clipPath adapts to each target, and non-overlapping
    clipped objects don't depend on painting order."""
    svg = b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
          <defs>
            <clipPath id="clip" clipPathUnits="objectBoundingBox">
              <rect x="0" y="0" width="0.5" height="1"/>
            </clipPath>
          </defs>
          %(first)s
          %(second)s
        </svg>'''
    blue_rect = (b'<rect x="0" y="0" width="100" height="100" fill="blue" '
                 b'clip-path="url(#clip)"/>')
    red_rect = (b'<rect x="100" y="0" width="50" height="100" fill="red" '
                b'clip-path="url(#clip)"/>')
    pixel = clip_pixel_getter(svg2png(
        bytestring=svg % {b'first': blue_rect, b'second': red_rect}))
    assert pixel(25, 50) == BLUE
    assert pixel(75, 50) == TRANSPARENT
    assert pixel(110, 50) == RED
    assert pixel(140, 50) == TRANSPARENT
    # Same result when the non-overlapping objects are swapped
    swapped = clip_pixel_getter(svg2png(
        bytestring=svg % {b'first': red_rect, b'second': blue_rect}))
    for x, y in ((25, 50), (75, 50), (110, 50), (140, 50)):
        assert swapped(x, y) == pixel(x, y)


def test_clip_path_use_and_transform():
    """Local use references and transforms work inside clipPath."""
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <rect id="shape" x="0" y="0" width="40" height="40"/>
            <clipPath id="clip">
              <use href="#shape" x="10" y="10"/>
              <rect x="0" y="0" width="20" height="20"
                    transform="translate(60, 60)"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue" clip-path="url(#clip)"/>
        </svg>'''))
    assert pixel(30, 30) == BLUE       # used shape, offset by x/y
    assert pixel(5, 5) == TRANSPARENT
    assert pixel(70, 70) == BLUE       # transformed rect
    assert pixel(40, 70) == TRANSPARENT


def test_clip_path_edge_cases():
    """Empty clips, zero-area bounding boxes and circular references
    finish safely."""
    # Empty clipPath: nothing is drawn
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="empty"/>
          </defs>
          <rect width="100" height="100" fill="blue"
                clip-path="url(#empty)"/>
        </svg>'''))
    assert pixel(50, 50) == TRANSPARENT

    # Zero-area bounding box with objectBoundingBox: nothing is drawn and
    # no invalid matrix is produced
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="box" clipPathUnits="objectBoundingBox">
              <rect width="1" height="1"/>
            </clipPath>
          </defs>
          <line x1="0" y1="0" x2="100" y2="0" stroke="blue"
                clip-path="url(#box)"/>
        </svg>'''))
    assert pixel(50, 0) == TRANSPARENT

    # Circular clip-path references terminate
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="loop-a" clip-path="url(#loop-b)">
              <rect width="100" height="100"/>
            </clipPath>
            <clipPath id="loop-b" clip-path="url(#loop-a)">
              <rect width="100" height="100"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue"
                clip-path="url(#loop-a)"/>
        </svg>'''))
    assert pixel(50, 50) == BLUE

    # Circular use references inside a clipPath terminate
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <g id="group">
              <use href="#group"/>
              <rect width="50" height="50"/>
            </g>
            <clipPath id="clip">
              <use href="#group"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue"
                clip-path="url(#clip)"/>
        </svg>'''))
    assert pixel(25, 25) == BLUE
    assert pixel(75, 75) == TRANSPARENT


def test_clip_path_vector_output():
    """PNG and vector exports show the same clipped region."""
    svg = b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="clip">
              <path clip-rule="evenodd"
                    d="M10 10 h80 v80 h-80 z M30 30 h40 v40 h-40 z"/>
              <rect x="60" y="60" width="30" height="30"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue" clip-path="url(#clip)"/>
        </svg>'''
    pixel = clip_pixel_getter(svg2png(bytestring=svg))
    vector_pixel = clip_pixel_getter(svg2png(bytestring=svg2svg(
        bytestring=svg)))
    for x, y in ((20, 20), (50, 50), (75, 75), (5, 5)):
        assert vector_pixel(x, y) == pixel(x, y)
    # Other vector formats render without error
    assert svg2pdf(bytestring=svg).startswith(MAGIC_NUMBERS['PDF'])
    assert SURFACES['PS'].convert(svg).startswith(MAGIC_NUMBERS['PS'])


def test_clip_path_no_side_effects():
    """Unclipped content and drawing state are not affected by clips."""
    pixel = clip_pixel_getter(svg2png(bytestring=b'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <clipPath id="clip">
              <rect x="0" y="0" width="50" height="50" fill="red"
                    stroke="black" opacity="0.5"/>
            </clipPath>
          </defs>
          <rect width="100" height="100" fill="blue"
                clip-path="url(#clip)"/>
          <path d="M60 60 h30 v30 h-30 z M70 70 h10 v10 h-10 z"
                fill="blue" fill-rule="evenodd"/>
        </svg>'''))
    # The clipped rect keeps its own color, not the clip child's one
    assert pixel(25, 25) == BLUE
    assert pixel(75, 25) == TRANSPARENT
    # The unclipped evenodd path drawn afterwards is unaffected
    assert pixel(65, 65) == BLUE
    assert pixel(75, 75) == TRANSPARENT
