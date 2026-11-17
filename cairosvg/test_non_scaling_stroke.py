"""Tests for the ``vector-effect="non-scaling-stroke"`` support."""

import io
import math

import pytest
from PIL import Image

from . import svg2png

# A document stacking several transformations: rotation, non-uniform scaling
# and a skew, applied through nested groups. The four supported shape kinds
# are drawn both with and without the vector effect, including a clip path.
MULTILAYER_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"
     viewBox="0 0 400 400">
  <defs>
    <clipPath id="clip">
      <rect x="0" y="0" width="400" height="400"/>
    </clipPath>
  </defs>
  <g transform="rotate(10 200 200)">
    <g transform="scale(2, 3)">
      <g transform="skewX(15) translate(10, 5)">
        <g clip-path="url(#clip)">
          <path d="M 20 25 L 120 25" stroke="black" stroke-width="6"
                fill="none" stroke-linecap="round"
                stroke-dasharray="12 8" {effect}/>
          <line x1="20" y1="55" x2="120" y2="55" stroke="black"
                stroke-width="6" {effect}/>
          <line x1="160" y1="20" x2="160" y2="110" stroke="black"
                stroke-width="6" {effect}/>
          <polyline points="20,85 90,85 90,115" fill="none" stroke="black"
                    stroke-width="6" stroke-linejoin="round" {effect}/>
          <polygon points="20,130 70,130 70,160 20,160" fill="red"
                   stroke="black" stroke-width="6" {effect}/>
        </g>
      </g>
    </g>
  </g>
</svg>"""


def render(svg, **kwargs):
    """Render an SVG string and return the RGBA image."""
    png = svg2png(bytestring=svg.encode(), **kwargs)
    return Image.open(io.BytesIO(png)).convert('RGBA')


def column_thickness(image, x):
    """Return the vertical run of opaque pixels in column ``x``."""
    pixels = image.load()
    rows = [y for y in range(image.height) if pixels[x, y][3]]
    return max(rows) - min(rows) + 1 if rows else 0


def column_run_thicknesses(image, x):
    """Return the lengths of the opaque runs found in column ``x``.

    Several shapes may be stacked in the same column: each disconnected run
    is measured independently.

    """
    pixels = image.load()
    runs = []
    start = None
    for y in range(image.height):
        if pixels[x, y][3]:
            if start is None:
                start = y
        elif start is not None:
            runs.append(y - start)
            start = None
    if start is not None:
        runs.append(image.height - start)
    return runs


def row_run_thicknesses(image, y):
    """Return the lengths of the opaque runs found in row ``y``."""
    pixels = image.load()
    runs = []
    start = None
    for x in range(image.width):
        if pixels[x, y][3]:
            if start is None:
                start = x
        elif start is not None:
            runs.append(x - start)
            start = None
    if start is not None:
        runs.append(image.width - start)
    return runs


def connected_components(image):
    """Return the lists of opaque pixels of each connected component."""
    pixels = image.load()
    width, height = image.size
    seen = bytearray(width * height)
    components = []
    for start_x in range(width):
        for start_y in range(height):
            index = start_x * height + start_y
            if seen[index] or not pixels[start_x, start_y][3]:
                continue
            stack = [(start_x, start_y)]
            seen[index] = 1
            points = []
            while stack:
                x, y = stack.pop()
                points.append((x, y))
                for next_x in (x - 1, x, x + 1):
                    for next_y in (y - 1, y, y + 1):
                        next_index = next_x * height + next_y
                        if (0 <= next_x < width and 0 <= next_y < height
                                and not seen[next_index]
                                and pixels[next_x, next_y][3]):
                            seen[next_index] = 1
                            stack.append((next_x, next_y))
            components.append(points)
    return components


def local_thickness(image, points):
    """Measure a component's stroke width along its local normal.

    The main orientation of the pixels is found by PCA and the width is
    sampled along the perpendicular direction, which keeps the measurement
    independent of the line angle.

    """
    pixels = image.load()
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    variance_x = sum((x - mean_x) ** 2 for x, y in points)
    variance_y = sum((y - mean_y) ** 2 for x, y in points)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in points)
    angle = 0.5 * math.atan2(2 * covariance, variance_x - variance_y)
    normal_x, normal_y = -math.sin(angle), math.cos(angle)

    def reach(center_x, center_y, direction):
        distance = 0
        while True:
            distance += 1
            x = int(round(center_x + direction * normal_x * distance))
            y = int(round(center_y + direction * normal_y * distance))
            if not (0 <= x < image.width and 0 <= y < image.height
                    and pixels[x, y][3]):
                return distance - 1

    widths = []
    step = max(1, len(points) // 5)
    for center_x, center_y in points[::step]:
        widths.append(
            reach(center_x, center_y, 1) + reach(center_x, center_y, -1) + 1)
    return max(widths)


def perpendicular_thickness(image, angle):
    """Measure the stroke width along the direction normal to a line."""
    pixels = image.load()
    normal = math.radians(angle + 90)
    dx, dy = math.cos(normal), math.sin(normal)
    center_x, center_y = image.width / 2, image.height / 2
    hits = []
    for distance in range(-max(image.size), max(image.size)):
        x = int(round(center_x + dx * distance))
        y = int(round(center_y + dy * distance))
        if 0 <= x < image.width and 0 <= y < image.height and pixels[x, y][3]:
            hits.append(distance)
    return max(hits) - min(hits) + 1 if hits else 0


def horizontal_dash_runs(image):
    """Return the painted run lengths of a horizontal dashed line."""
    pixels = image.load()
    row = max(
        range(image.height),
        key=lambda y: sum(pixels[x, y][3] > 0 for x in range(image.width)))
    runs = []
    current = 0
    for x in range(image.width):
        if pixels[x, row][3]:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def test_non_scaling_stroke_keeps_width_under_non_uniform_scale():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
  <g transform="scale(2, 3)">
    <line x1="10" y1="20" x2="90" y2="20" stroke="black" stroke-width="6"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    image = render(svg)
    # 6 user units must stay 6 pixels: the y scale of 3 is ignored.
    assert column_thickness(image, 100) == 6

    default = render(svg.replace(
        ' vector-effect="non-scaling-stroke"', ''))
    assert column_thickness(default, 100) == 18


def test_non_scaling_stroke_uniform_under_skew():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">
  <g transform="skewX(30) scale(2, 3)">
    <path d="M 20 40 L 120 40" stroke="black" stroke-width="6" fill="none"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    image = render(svg)
    thicknesses = {
        column_thickness(image, x)
        for x in range(120, 280, 20) if column_thickness(image, x)}
    assert thicknesses <= {5, 6, 7}

    default = render(svg.replace(
        ' vector-effect="non-scaling-stroke"', ''))
    default_thicknesses = [
        column_thickness(default, x) for x in range(120, 280, 20)]
    assert max(t for t in default_thicknesses if t) > 10


def test_non_scaling_stroke_uniform_under_rotation():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">
  <g transform="rotate(20 150 150) scale(2, 3)">
    <line x1="30" y1="100" x2="130" y2="100" stroke="black" stroke-width="8"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    thickness = perpendicular_thickness(render(svg), 20)
    assert 7 <= thickness <= 9

    default = perpendicular_thickness(
        render(svg.replace(' vector-effect="non-scaling-stroke"', '')), 20)
    assert default > 20


def test_multilayer_document_renders_uniform_strokes():
    image = render(MULTILAYER_SVG.format(
        effect='vector-effect="non-scaling-stroke"'))
    pixels = image.load()
    # Rotation, non-uniform scaling and a skew are stacked together: every
    # straight painted component keeps a width close to the requested 6 user
    # units. Only corner components, which overlap two strokes, are wider.
    thicknesses = sorted(
        local_thickness(image, component)
        for component in connected_components(image)
        if len(component) >= 80)
    # A 6-pixel stroke covers 7 opaque rows once antialiased.
    assert min(thicknesses) == 7
    assert thicknesses.count(7) >= 4
    # The shapes are still painted somewhere in the image.
    assert any(pixels[x, y][3] for x in range(image.width)
               for y in range(image.height))

    default = render(MULTILAYER_SVG.format(effect=''))
    default_thicknesses = sorted(
        local_thickness(default, component)
        for component in connected_components(default)
        if len(component) >= 80)
    assert min(default_thicknesses) > min(thicknesses)


def test_dashes_are_not_distorted():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100">
  <g transform="scale(3, 2)">
    <line x1="10" y1="30" x2="120" y2="30" stroke="black" stroke-width="4"
          stroke-dasharray="10 6" {effect}/>
  </g>
</svg>"""
    runs = horizontal_dash_runs(
        render(svg.format(effect='vector-effect="non-scaling-stroke"')))
    assert runs and all(9 <= run <= 11 for run in runs)

    default_runs = horizontal_dash_runs(render(svg.format(effect='')))
    assert default_runs and all(28 <= run <= 32 for run in default_runs)


def test_clip_path_still_clips_non_scaling_stroke():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
  <defs>
    <clipPath id="clip"><rect x="0" y="0" width="60" height="100"/></clipPath>
  </defs>
  <g clip-path="url(#clip)" transform="scale(2, 2)">
    <line x1="5" y1="40" x2="90" y2="40" stroke="black" stroke-width="4"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    image = render(svg)
    pixels = image.load()
    assert any(pixels[50, y][3] for y in range(image.height))
    assert not any(pixels[130, y][3] for y in range(image.height))


def test_fill_still_follows_transformations():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">
  <g transform="scale(2, 3)">
    <path d="M 10 10 h40 v30 h-40 z" fill="red"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    image = render(svg)
    pixels = image.load()
    red = [
        (x, y) for x in range(image.width) for y in range(image.height)
        if pixels[x, y][0] > 200 and pixels[x, y][1] < 60]
    xs = [point[0] for point in red]
    ys = [point[1] for point in red]
    # 40x30 user units become 80x90 pixels through the group transform.
    assert max(xs) - min(xs) + 1 == 80
    assert max(ys) - min(ys) + 1 == 90


def test_group_opacity_is_preserved():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <g transform="scale(2, 2)" opacity="0.5">
    <line x1="10" y1="40" x2="45" y2="40" stroke="black" stroke-width="4"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    image = render(svg)
    pixels = image.load()
    alphas = {
        pixels[40, y][3]
        for y in range(image.height) if 0 < pixels[40, y][3] < 255}
    assert alphas == {128}


def test_export_scale_scales_non_scaling_stroke():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">
  <g transform="scale(2, 3)">
    <line x1="5" y1="10" x2="45" y2="10" stroke="black" stroke-width="4"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    single = max(
        column_thickness(render(svg, scale=1), x) for x in range(20, 90))
    double = max(
        column_thickness(render(svg, scale=2), x) for x in range(40, 180))
    assert single == 4
    assert double == 8


def test_dpi_keeps_its_original_behavior():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">
  <g transform="scale(2, 3)">
    <line x1="5" y1="20" x2="90" y2="20" stroke="black" stroke-width="4pt"
          vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""

    def thickness(dpi):
        image = render(svg, dpi=dpi)
        return max(
            column_thickness(image, x)
            for x in range(0, image.width, 5))

    assert thickness(192) == 2 * thickness(96)


def test_round_caps_and_joins_keep_their_shape():
    cap_svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="300" height="120">
  <g transform="scale(2, 3)">
    <line x1="20" y1="20" x2="100" y2="20" stroke="black" stroke-width="8"
          stroke-linecap="round" {effect}/>
  </g>
</svg>"""
    image = render(cap_svg.format(
        effect='vector-effect="non-scaling-stroke"'))
    pixels = image.load()
    xs = [x for x in range(80) for y in range(image.height) if pixels[x, y][3]]
    # The round cap extends by width / 2 = 4 pixels beyond the endpoint at
    # x = 40, so painted pixels start at x = 36.
    assert min(xs) == 36

    join_svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300">
  <g transform="scale(2, 3)">
    <polyline points="20,20 90,20 90,90" fill="none" stroke="black"
              stroke-width="8" stroke-linejoin="round"
              vector-effect="non-scaling-stroke"/>
  </g>
</svg>"""
    join_image = render(join_svg)
    thicknesses = [
        column_thickness(join_image, x)
        for x in range(60, 170) if column_thickness(join_image, x)]
    assert thicknesses and max(thicknesses) <= 10


@pytest.mark.parametrize('tag,markup', (
    ('path', '<path d="M 5 10 L 45 10" stroke="black" stroke-width="4"/>'),
    ('line', '<line x1="5" y1="10" x2="45" y2="10" stroke="black" '
             'stroke-width="4"/>'),
    ('polyline', '<polyline points="5,10 45,10" fill="none" stroke="black" '
                 'stroke-width="4"/>'),
    ('polygon', '<polygon points="5,10 45,10 45,20" fill="none" '
                'stroke="black" stroke-width="4"/>'),
))
def test_supported_shapes(tag, markup):
    svg = f"""\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">
  <g transform="scale(2, 3)">
    {markup}
  </g>
</svg>"""
    with_effect = svg.replace(
        '/>', ' vector-effect="non-scaling-stroke"/>')
    nss_runs = column_run_thicknesses(render(with_effect), 50)
    default_runs = column_run_thicknesses(render(svg), 50)
    assert nss_runs and max(nss_runs) <= 6
    assert max(default_runs) >= 12


def test_attribute_can_be_set_in_style():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">
  <g transform="scale(2, 3)">
    <line x1="5" y1="10" x2="45" y2="10" stroke="black" stroke-width="4"
          style="vector-effect: non-scaling-stroke"/>
  </g>
</svg>"""
    assert column_thickness(render(svg), 50) == 4


def test_unsupported_and_missing_effect_keep_default_rendering():
    svg = """\
<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">
  <g transform="scale(2, 3)">
    <circle cx="20" cy="10" r="8" stroke="black" stroke-width="4"/>
  </g>
</svg>"""
    with_effect = svg.replace(
        '/>', ' vector-effect="non-scaling-stroke"/>')
    # circle and rect are not supported yet: the attribute is ignored.
    assert render(svg).tobytes() == render(with_effect).tobytes()

    unknown = svg.replace('/>', ' vector-effect="none"/>')
    assert render(svg).tobytes() == render(unknown).tobytes()


def test_gradient_stroke_keeps_its_device_space_position():
    gradient = (
        '<defs><linearGradient id="gradient" '
        'gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="200" y2="0">'
        '<stop offset="0" stop-color="red"/>'
        '<stop offset="1" stop-color="blue"/></linearGradient></defs>')
    svg = f"""\
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">{gradient}
  <g transform="scale(2, 3)">
    <line x1="5" y1="20" x2="90" y2="20" stroke="url(#gradient)" stroke-width="6"
          {{effect}}/>
  </g>
</svg>"""
    default = render(svg.format(effect=''))
    non_scaling = render(svg.format(
        effect='vector-effect="non-scaling-stroke"'))
    default_pixels = default.load()
    non_scaling_pixels = non_scaling.load()
    default_row = next(
        y for y in range(default.height)
        if any(default_pixels[x, y][3] for x in range(default.width)))
    non_scaling_row = next(
        y for y in range(non_scaling.height)
        if any(non_scaling_pixels[x, y][3]
               for x in range(non_scaling.width)))
    # The gradient spans the same device positions while the stroke itself
    # keeps a 6-pixel width instead of the scaled 18-pixel width.
    mismatches = sum(
        abs(default_pixels[x, default_row][2]
            - non_scaling_pixels[x, non_scaling_row][2]) > 12
        for x in range(0, 200, 5))
    assert mismatches == 0
    assert column_thickness(non_scaling, 100) == 6
    assert column_thickness(default, 100) == 18
