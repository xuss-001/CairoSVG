"""Tests for SVG Gaussian blur filters."""

from io import BytesIO

import pytest
from PIL import Image

from . import svg2png


def render(svg, **kwargs):
    """Render an SVG string and return its RGBA pixels."""
    png = svg2png(bytestring=svg.encode(), **kwargs)
    return Image.open(BytesIO(png)).convert('RGBA')


def alpha_bbox(image):
    """Return the bounding box of non-transparent pixels."""
    return image.getchannel('A').getbbox()


def test_blur_region_color_and_transparent_edges():
    """Blur extends beyond the shape, is clipped, and keeps source colors."""
    image = render('''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <filter id="blur" filterUnits="userSpaceOnUse"
                    x="30" y="30" width="40" height="40">
              <feGaussianBlur in="SourceGraphic" stdDeviation="8"/>
            </filter>
          </defs>
          <rect x="40" y="40" width="20" height="20" fill="#ff0000"
                filter="url(#blur)"/>
        </svg>
    ''')

    assert image.getpixel((50, 50))[3] > 100
    assert image.getpixel((35, 50))[3] > 0
    assert image.getpixel((29, 50))[3] == 0
    assert image.getpixel((70, 50))[3] == 0

    pixels = image.tobytes()
    for index in range(0, len(pixels), 4):
        red, green, blue, alpha = pixels[index:index + 4]
        if alpha:
            assert red == 255
            assert green == 0
            assert blue == 0


@pytest.mark.parametrize(
    'deviation, horizontal, vertical',
    (('6 0', True, False), ('0 6', False, True)))
def test_blur_standard_deviation_axes(deviation, horizontal, vertical):
    """Each standard deviation controls its own axis and zero disables it."""
    image = render(f'''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <filter id="blur" filterUnits="userSpaceOnUse"
                    x="10" y="10" width="80" height="80">
              <feGaussianBlur stdDeviation="{deviation}"/>
            </filter>
          </defs>
          <rect x="40" y="40" width="20" height="20" fill="red"
                filter="url(#blur)"/>
        </svg>
    ''')

    assert bool(image.getpixel((33, 50))[3]) is horizontal
    assert bool(image.getpixel((50, 33))[3]) is vertical


def test_blur_on_group():
    """A filter on a group blurs all group children together."""
    image = render('''
        <svg xmlns="http://www.w3.org/2000/svg" width="120" height="100">
          <defs>
            <filter id="blur">
              <feGaussianBlur stdDeviation="5"/>
            </filter>
          </defs>
          <g filter="url(#blur)">
            <rect x="20" y="40" width="20" height="20" fill="red"/>
            <rect x="70" y="40" width="20" height="20" fill="red"/>
          </g>
        </svg>
    ''')

    assert image.getpixel((15, 50))[3] > 0
    assert image.getpixel((95, 50))[3] > 0
    assert image.getpixel((45, 50))[3] > 0


def test_blur_object_bounding_box_units():
    """Object bounding box units control the filter region."""
    image = render('''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <filter id="blur" filterUnits="objectBoundingBox"
                    x="-0.5" y="-0.5" width="2" height="2">
              <feGaussianBlur stdDeviation="8"/>
            </filter>
          </defs>
          <rect x="40" y="40" width="20" height="20" fill="red"
                filter="url(#blur)"/>
        </svg>
    ''')

    assert image.getpixel((31, 50))[3] > 0
    assert image.getpixel((29, 50))[3] == 0
    assert image.getpixel((69, 50))[3] > 0
    assert image.getpixel((71, 50))[3] == 0


def test_blur_primitive_units():
    """Object bounding box primitive units scale each deviation axis."""
    image = render('''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <filter id="blur" filterUnits="userSpaceOnUse"
                    primitiveUnits="objectBoundingBox"
                    x="10" y="10" width="80" height="80">
              <feGaussianBlur stdDeviation="0.2 0.05"/>
            </filter>
          </defs>
          <rect x="40" y="40" width="20" height="20" fill="red"
                filter="url(#blur)"/>
        </svg>
    ''')

    assert image.getpixel((35, 50))[3] > 0
    assert image.getpixel((50, 35))[3] == 0


def test_blur_with_transform_and_scale():
    """Transforms and output scaling keep blur position and size correct."""
    svg = '''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <filter id="blur" filterUnits="userSpaceOnUse"
                    x="0" y="0" width="100" height="100">
              <feGaussianBlur stdDeviation="4"/>
            </filter>
          </defs>
          <rect x="30" y="40" width="20" height="20" fill="red"
                transform="translate(10 20)" filter="url(#blur)"/>
        </svg>
    '''
    image = render(svg)

    assert image.getpixel((45, 70))[3] > 200
    assert image.getpixel((35, 70))[3] > 0
    assert image.getpixel((25, 70))[3] == 0

    bbox = alpha_bbox(image)
    for scaled in (
            render(svg, output_width=200, output_height=200),
            render(svg, scale=2)):
        scaled_bbox = alpha_bbox(scaled)
        assert scaled_bbox[0] == pytest.approx(2 * bbox[0], abs=3)
        assert scaled_bbox[1] == pytest.approx(2 * bbox[1], abs=3)
        assert scaled_bbox[2] == pytest.approx(2 * bbox[2], abs=3)
        assert scaled_bbox[3] == pytest.approx(2 * bbox[3], abs=3)


def test_blur_opacity_is_applied_once():
    """Element opacity is not applied to both source and filtered result."""
    image = render('''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs>
            <filter id="blur" filterUnits="userSpaceOnUse"
                    x="0" y="0" width="100" height="100">
              <feGaussianBlur stdDeviation="0"/>
            </filter>
          </defs>
          <rect x="20" y="20" width="60" height="60" fill="red"
                opacity="0.5" filter="url(#blur)"/>
        </svg>
    ''')

    assert image.getpixel((50, 50))[3] == pytest.approx(128, abs=1)


def test_blur_filter_reused_by_multiple_objects():
    """Reusing a filter does not leak state between its users."""
    image = render('''
        <svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">
          <defs>
            <filter id="blur">
              <feGaussianBlur stdDeviation="2"/>
            </filter>
          </defs>
          <rect x="10" y="20" width="20" height="20" fill="red"
                filter="url(#blur)"/>
          <rect x="70" y="20" width="20" height="20" fill="red"
                filter="url(#blur)"/>
        </svg>
    ''')

    first = image.crop((0, 10, 40, 50))
    second = image.crop((60, 10, 100, 50))
    assert first.tobytes() == second.tobytes()


@pytest.mark.parametrize(
    'deviation', ('-1', '1 -2', 'one', '1 2 3', 'nan', 'inf'))
def test_invalid_blur_parameters_raise(deviation):
    """Invalid standard deviation values raise an error."""
    svg = f'''
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">
          <defs>
            <filter id="blur">
              <feGaussianBlur stdDeviation="{deviation}"/>
            </filter>
          </defs>
          <rect x="5" y="5" width="10" height="10"
                filter="url(#blur)"/>
        </svg>
    '''
    with pytest.raises(ValueError):
        svg2png(bytestring=svg.encode())
