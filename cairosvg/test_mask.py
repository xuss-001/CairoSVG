"""Tests for SVG mask rendering."""

import io
from base64 import b64encode

import pytest
from PIL import Image

from . import svg2pdf, svg2png, svg2ps


def render(svg, **kwargs):
    """Render SVG bytes and return an RGBA image."""
    return Image.open(io.BytesIO(svg2png(svg, **kwargs))).convert('RGBA')


def assert_alpha(image, position, expected, tolerance=2):
    """Check the alpha channel at ``position``."""
    assert image.getpixel(position)[3] == pytest.approx(
        expected, abs=tolerance)


def test_mask_type_in_attribute_style_and_stylesheet():
    """Apply alpha and luminance mask types from attributes and CSS."""
    template = '''\
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">
          {style}
          <rect width="20" height="20" fill="red" mask="url(#mask)"/>
          <mask id="mask" maskUnits="userSpaceOnUse"
                x="0" y="0" width="20" height="20" {attributes}>
            <rect width="20" height="20" fill="black"/>
          </mask>
        </svg>
    '''
    cases = (
        ('', '', 255),
        ('mask-type="alpha"', '', 255),
        ('mask-type="alpha"', 'style="mask-type: alpha"', 255),
        ('mask-type="luminance"', '', 0),
        ('style="mask-type: luminance"', '', 0),
        ('', '<style>#mask { mask-type: luminance }</style>', 0),
    )
    for attributes, style, expected in cases:
        image = render(template.format(
            attributes=attributes, style=style).encode())
        assert_alpha(image, (10, 10), expected)

    inherited = template.format(attributes='', style='').replace(
        '<svg ', '<svg style="mask-type: luminance" ')
    assert_alpha(render(inherited.encode()), (10, 10), 255)


@pytest.mark.parametrize(('color', 'opacity', 'expected'), (
    ('black', 1, 0),
    ('white', 1, 255),
    ('#ff0000', 1, 54),
    ('#00ff00', 1, 182),
    ('#0000ff', 1, 18),
    ('white', 0.5, 128),
    ('black', 0.5, 0),
))
def test_luminance_uses_color_and_alpha(color, opacity, expected):
    """Combine color luminance and alpha in luminance masks."""
    svg = f'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">
          <rect width="20" height="20" fill="red" mask="url(#mask)"/>
          <mask id="mask" mask-type="luminance" maskUnits="userSpaceOnUse"
                x="0" y="0" width="20" height="20">
            <rect width="20" height="20" fill="{color}"
                  fill-opacity="{opacity}"/>
          </mask>
        </svg>
    '''.encode()
    assert_alpha(render(svg), (10, 10), expected)


def test_luminance_composites_groups_gradients_and_images():
    """Composite all mask children before applying luminance."""
    source_image = Image.new('RGBA', (2, 2))
    source_image.putpixel((0, 0), (255, 255, 255, 255))
    source_image.putpixel((1, 0), (0, 0, 0, 255))
    source_image.putpixel((0, 1), (255, 255, 255, 255))
    source_image.putpixel((1, 1), (0, 0, 0, 255))
    image_buffer = io.BytesIO()
    source_image.save(image_buffer, 'PNG')
    image_data = b64encode(image_buffer.getvalue()).decode('ascii')

    svg = f'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">
          <rect width="40" height="20" fill="red" mask="url(#mask)"/>
          <mask id="mask" mask-type="luminance" maskUnits="userSpaceOnUse"
                x="0" y="0" width="40" height="20">
            <defs>
              <linearGradient id="gradient" x1="0" x2="1">
                <stop offset="0" stop-color="white"/>
                <stop offset="1" stop-color="black"/>
              </linearGradient>
            </defs>
            <g>
              <rect width="10" height="20" fill="url(#gradient)"/>
              <rect x="10" width="10" height="20" fill="white"/>
            </g>
            <image x="20" width="20" height="20" preserveAspectRatio="none"
                   href="data:image/png;base64,{image_data}"/>
          </mask>
        </svg>
    '''.encode()
    image = render(svg)
    assert_alpha(image, (1, 10), 230, tolerance=30)
    assert_alpha(image, (9, 10), 25, tolerance=30)
    assert_alpha(image, (15, 10), 255)
    assert_alpha(image, (25, 10), 255, tolerance=20)
    assert_alpha(image, (35, 10), 0)


def test_luminance_mask_region_units_and_transforms():
    """Respect mask regions, units, content units and transforms."""
    user_space_svg = b'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="80" height="20">
          <rect width="80" height="20" fill="red" mask="url(#mask)"/>
          <mask id="mask" mask-type="luminance" maskUnits="userSpaceOnUse"
                x="20" y="0" width="40" height="20">
            <rect width="80" height="20" fill="white"/>
          </mask>
        </svg>
    '''
    image = render(user_space_svg)
    assert_alpha(image, (10, 10), 0)
    assert_alpha(image, (40, 10), 255)
    assert_alpha(image, (70, 10), 0)

    unit_svg = b'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="80" height="20">
          <rect width="80" height="20" fill="red" mask="url(#mask)"/>
          <mask id="mask" mask-type="luminance" maskUnits="userSpaceOnUse"
                x="5mm" y="0" width="10mm" height="20">
            <rect width="80" height="20" fill="white"/>
          </mask>
        </svg>
    '''
    image = render(unit_svg)
    assert_alpha(image, (10, 10), 0)
    assert_alpha(image, (40, 10), 255)
    assert_alpha(image, (70, 10), 0)

    bounding_box_svg = b'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">
          <rect width="40" height="20" fill="red" mask="url(#mask)"/>
          <mask id="mask" mask-type="luminance" x="0" y="0"
                width=".5" height="1">
            <rect width="40" height="20" fill="white"/>
          </mask>
        </svg>
    '''
    image = render(bounding_box_svg)
    assert_alpha(image, (5, 10), 255)
    assert_alpha(image, (25, 10), 0)

    content_units_svg = b'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">
          <rect x="10" width="20" height="20" fill="red" mask="url(#mask)"/>
          <mask id="mask" mask-type="luminance"
                maskContentUnits="objectBoundingBox">
            <rect width=".5" height="1" fill="white"/>
          </mask>
        </svg>
    '''
    image = render(content_units_svg)
    assert_alpha(image, (15, 10), 255)
    assert_alpha(image, (25, 10), 0)

    transformed_svg = b'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">
          <g transform="translate(10, 0)">
            <rect x="10" width="20" height="20" fill="red"
                  mask="url(#mask)"/>
          </g>
          <mask id="mask" mask-type="luminance" maskUnits="userSpaceOnUse"
                x="10" y="0" width="10" height="20">
            <rect x="10" width="20" height="20" fill="white"/>
          </mask>
        </svg>
    '''
    image = render(transformed_svg)
    assert_alpha(image, (15, 10), 0)
    assert_alpha(image, (25, 10), 255)
    assert_alpha(image, (35, 10), 0)


def test_shared_mask_is_independent_of_draw_order():
    """Use the same mask for differently sized objects in any order."""
    template = '''\
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">
          <rect width="40" height="20" fill="#dddddd"/>
          {objects}
          <mask id="mask" mask-type="luminance"
                maskContentUnits="objectBoundingBox">
            <rect width="1" height="1" fill="white"/>
          </mask>
        </svg>
    '''
    first = (
        '<rect width="10" height="20" fill="red" mask="url(#mask)"/>'
        '<rect x="10" width="30" height="20" fill="blue" '
        'mask="url(#mask)"/>')
    second = (
        '<rect x="10" width="30" height="20" fill="blue" '
        'mask="url(#mask)"/>'
        '<rect width="10" height="20" fill="red" mask="url(#mask)"/>')
    first_image = render(template.format(objects=first).encode())
    second_image = render(template.format(objects=second).encode())
    assert first_image.tobytes() == second_image.tobytes()
    assert first_image.getpixel((5, 10)) == (255, 0, 0, 255)
    assert first_image.getpixel((25, 10)) == (0, 0, 255, 255)


@pytest.mark.parametrize('mask_type', ('alpha', 'luminance'))
def test_mask_opacity_is_applied_once(mask_type):
    """Do not multiply an object's opacity twice when it has a mask."""
    svg = f'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">
          <rect width="20" height="20" fill="red" opacity=".5"
                fill-opacity=".5" mask="url(#mask)"/>
          <mask id="mask" mask-type="{mask_type}" maskUnits="userSpaceOnUse"
                x="0" y="0" width="20" height="20">
            <rect width="20" height="20" fill="white"/>
          </mask>
        </svg>
    '''.encode()
    assert_alpha(render(svg), (10, 10), 64)


def test_mask_type_keeps_crop_scale_and_non_png_formats_compatible():
    """Keep clipping, scaling and PDF/PS fallback behavior."""
    svg = b'''\
        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">
          <defs>
            <clipPath id="clip"><rect width="10" height="20"/></clipPath>
          </defs>
          <rect width="20" height="20" fill="red" clip-path="url(#clip)"
                mask="url(#mask)"/>
          <mask id="mask" mask-type="luminance" maskUnits="userSpaceOnUse"
                x="0" y="0" width="20" height="20">
            <rect width="20" height="20" fill="white"/>
          </mask>
        </svg>
    '''
    image = render(svg, scale=2)
    assert image.size == (40, 40)
    assert_alpha(image, (10, 20), 255)
    assert_alpha(image, (30, 20), 0)

    assert svg2pdf(svg).startswith(b'%PDF')
    assert svg2ps(svg).startswith(b'%!')
