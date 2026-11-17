"""Tests for SVG mask rendering."""

import base64
import io

import pytest
from PIL import Image

from . import svg2png


def render(svg, **kwargs):
    """Render an SVG string as a Pillow RGBA image."""
    png = svg2png(bytestring=svg.encode(), **kwargs)
    return Image.open(io.BytesIO(png)).convert('RGBA')


def assert_alpha(image, point, expected, tolerance=2):
    """Assert that the pixel alpha at ``point`` is close to ``expected``."""
    alpha = image.getpixel(point)[3]
    assert abs(alpha - expected) <= tolerance


@pytest.mark.parametrize('mask_attributes', (
    'mask-type="luminance"',
    'style="mask-type: luminance"',
))
def test_luminance_mask_type(mask_attributes):
    """Luminance masks use both color luminance and alpha."""
    svg = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
  <defs>
    <mask id="mask" {mask_attributes}
          maskUnits="userSpaceOnUse" x="0" y="0" width="20" height="10">
      <rect x="0" y="0" width="10" height="10" fill="black"/>
      <rect x="10" y="0" width="10" height="10" fill="white"/>
    </mask>
  </defs>
  <rect width="20" height="10" fill="red" mask="url(#mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (5, 5), 0)
    assert_alpha(image, (15, 5), 255)


def test_alpha_mask_type_uses_opacity_only():
    """Alpha masks ignore the color of opaque mask content."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
  <defs>
    <mask id="mask" mask-type="alpha"
          maskUnits="userSpaceOnUse" x="0" y="0" width="20" height="10">
      <rect x="0" y="0" width="10" height="10" fill="black"/>
      <rect x="10" y="0" width="10" height="10" fill="blue"
            fill-opacity="0"/>
    </mask>
  </defs>
  <rect width="20" height="10" fill="red" mask="url(#mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (5, 5), 255)
    assert_alpha(image, (15, 5), 0)


def test_luminance_color_transparency_and_object_opacity():
    """Luminance includes transparency and object opacity is applied once."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="30" height="10">
  <defs>
    <mask id="mask" mask-type="luminance"
          maskUnits="userSpaceOnUse" x="0" y="0" width="30" height="10">
      <rect x="0" y="0" width="10" height="10" fill="lime"/>
      <rect x="10" y="0" width="10" height="10" fill="white"
            fill-opacity="0.5"/>
      <rect x="20" y="0" width="10" height="10" fill="red"/>
    </mask>
  </defs>
  <rect width="30" height="10" fill="red"
        opacity="0.5" mask="url(#mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (5, 5), round(255 * 0.7154 * 0.5))
    assert_alpha(image, (15, 5), round(255 * 0.5 * 0.5))
    assert_alpha(image, (25, 5), round(255 * 0.2125 * 0.5))


def test_mask_composites_group_gradient_and_image():
    """Groups, gradients and images are composited in mask content."""
    image_file = io.BytesIO()
    Image.new('RGB', (10, 10), 'white').save(image_file, 'PNG')
    image_url = 'data:image/png;base64,{}'.format(
        base64.b64encode(image_file.getvalue()).decode('ascii'))
    svg = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="30" height="10">
  <defs>
    <linearGradient id="gradient" gradientUnits="userSpaceOnUse"
                    x1="0" y1="0" x2="10" y2="0">
      <stop offset="0" stop-color="black"/>
      <stop offset="1" stop-color="white"/>
    </linearGradient>
    <mask id="mask" mask-type="luminance"
          maskUnits="userSpaceOnUse" x="0" y="0" width="30" height="10">
      <g>
        <rect x="0" y="0" width="10" height="10" fill="url(#gradient)"/>
        <image href="{image_url}" x="10" y="0" width="10" height="10"/>
        <rect x="20" y="0" width="10" height="10" fill="black"/>
      </g>
    </mask>
  </defs>
  <rect width="30" height="10" fill="red" mask="url(#mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (1, 5), 38, tolerance=4)
    assert_alpha(image, (5, 5), 140, tolerance=4)
    assert_alpha(image, (8, 5), 217, tolerance=4)
    assert_alpha(image, (15, 5), 255)
    assert_alpha(image, (25, 5), 0)


def test_mask_units_regions_and_object_transform():
    """Mask regions, content units and object transforms use the same space."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="50" height="30">
  <defs>
    <mask id="object-mask" mask-type="luminance"
          x="0" y="0" width="0.5" height="1"
          maskContentUnits="objectBoundingBox">
      <rect x="0" y="0" width="0.5" height="1" fill="white"/>
    </mask>
    <mask id="user-mask" mask-type="luminance"
          maskUnits="userSpaceOnUse" x="30" y="20" width="10" height="10"
          maskContentUnits="userSpaceOnUse">
      <rect x="30" y="20" width="10" height="10" fill="white"/>
    </mask>
  </defs>
  <rect x="5" y="5" width="20" height="10" fill="red"
        transform="translate(5 5)" mask="url(#object-mask)"/>
  <rect x="0" y="0" width="50" height="30" fill="blue"
        mask="url(#user-mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (12, 12), 255)
    assert_alpha(image, (22, 12), 0)
    assert_alpha(image, (25, 10), 0)
    assert_alpha(image, (35, 25), 255)


def test_repeated_mask_references_are_order_independent():
    """Reusing a mask gives the same result in any drawing order."""
    first = (
        '<rect x="10" y="10" width="20" height="10" fill="red" '
        'mask="url(#mask)"/>')
    second = (
        '<rect x="40" y="20" width="20" height="10" fill="blue" '
        'opacity="0.5" mask="url(#mask)"/>')

    def render_order(reversed_):
        elements = second + first if reversed_ else first + second
        return render(f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="70" height="40">
  <defs>
    <mask id="mask" mask-type="luminance" x="0" y="0" width="1" height="1"
          maskContentUnits="objectBoundingBox">
      <rect x="0" y="0" width="0.5" height="1" fill="white"/>
    </mask>
  </defs>
  {elements}
</svg>
''')

    normal = render_order(False)
    reversed_ = render_order(True)
    points = ((12, 12), (22, 12), (42, 22), (52, 22))
    assert [normal.getpixel(point) for point in points] == [
        reversed_.getpixel(point) for point in points]
    assert_alpha(normal, (12, 12), 255)
    assert_alpha(normal, (22, 12), 0)
    assert_alpha(normal, (42, 22), 128)
    assert_alpha(normal, (52, 22), 0)


def test_mask_default_keeps_alpha_behavior():
    """A mask without mask-type keeps the historical alpha behavior."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">
  <defs>
    <mask id="mask" maskUnits="userSpaceOnUse"
          x="0" y="0" width="20" height="10">
      <rect x="0" y="0" width="10" height="10" fill="black"/>
      <rect x="10" y="0" width="10" height="10" fill="white"
            fill-opacity="0.5"/>
    </mask>
  </defs>
  <rect width="20" height="10" fill="red" mask="url(#mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (5, 5), 255)
    assert_alpha(image, (15, 5), 128)


@pytest.mark.parametrize('mask_attributes', (
    '',
    'mask-type="alpha"',
    'mask-type="luminance"',
))
def test_mask_object_opacity_is_applied_once(mask_attributes):
    """Object opacity is not multiplied into both source and mask twice."""
    svg = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
  <defs>
    <mask id="mask" {mask_attributes}
          maskUnits="userSpaceOnUse" x="0" y="0" width="10" height="10">
      <rect width="10" height="10" fill="white"/>
    </mask>
  </defs>
  <rect width="10" height="10" fill="red"
        opacity="0.5" mask="url(#mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (5, 5), 128)


def test_mask_image_opacity_is_applied_once():
    """Image opacity is applied once when an image is masked."""
    image_file = io.BytesIO()
    Image.new('RGB', (10, 10), 'white').save(image_file, 'PNG')
    image_url = 'data:image/png;base64,{}'.format(
        base64.b64encode(image_file.getvalue()).decode('ascii'))
    svg = f'''\
<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
  <defs>
    <mask id="mask" mask-type="luminance"
          maskUnits="userSpaceOnUse" x="0" y="0" width="10" height="10">
      <rect width="10" height="10" fill="white"/>
    </mask>
  </defs>
  <image href="{image_url}" width="10" height="10"
         opacity="0.5" mask="url(#mask)"/>
</svg>
'''
    image = render(svg)
    assert_alpha(image, (5, 5), 128)


def test_mask_with_export_scale_and_clipping():
    """Masking keeps working with existing export and clipping options."""
    svg = '''\
<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
  <defs>
    <clipPath id="clip">
      <rect x="0" y="0" width="8" height="10"/>
    </clipPath>
    <mask id="mask" mask-type="luminance"
          maskUnits="userSpaceOnUse" x="0" y="0" width="10" height="10">
      <rect x="0" y="0" width="5" height="10" fill="white"/>
      <rect x="5" y="0" width="5" height="10" fill="black"/>
    </mask>
  </defs>
  <rect width="10" height="10" fill="red"
        clip-path="url(#clip)" mask="url(#mask)"/>
</svg>
'''
    image = render(svg, scale=2)
    assert image.size == (20, 20)
    assert_alpha(image, (4, 10), 255)
    assert_alpha(image, (14, 10), 0)
    assert_alpha(image, (18, 10), 0)
