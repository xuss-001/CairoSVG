"""Tests for SVG marker placement and automatic orientation."""

import io
import math

import pytest
from PIL import Image

from . import parser, surface, svg2pdf, svg2png, svg2svg


SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="140">'
    '{element}</svg>')

MARKER_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="140">'
    '<defs><marker id="arrow" markerUnits="userSpaceOnUse" '
    'markerWidth="16" markerHeight="16" refX="0" refY="8" '
    'orient="{orient}"><path d="M0 0 L16 8 L0 16 Z" fill="red"/>'
    '</marker></defs><path d="{path}" fill="none" stroke="none" '
    '{markers}/></svg>')


def path_vertices(path):
    """Return the marker vertices computed while drawing ``path``."""
    return element_vertices(f'<path d="{path}" fill="none"/>')


def element_vertices(element):
    """Return the marker vertices computed while drawing ``element``."""
    tree = parser.Tree(
        bytestring=SVG_TEMPLATE.format(element=element).encode())
    surface.PNGSurface(tree, io.BytesIO(), 96)
    return tree.children[0].vertices


def angles(path):
    """Return marker angles, in radians, for ``path``."""
    return [(vertex.angle, vertex.position) for vertex in path_vertices(path)]


def assert_angle(actual, expected):
    """Assert that two angles are equal modulo a full turn."""
    difference = (actual - expected + math.pi) % (2 * math.pi) - math.pi
    assert abs(difference) < 1e-6


def render_marker(path, orient='auto', markers='marker-start="url(#arrow)"'):
    """Render ``path`` with an asymmetric arrow marker."""
    return MARKER_TEMPLATE.format(
        path=path, orient=orient, markers=markers).encode()


def red_centroid_angle(png, anchor):
    """Return the angle from ``anchor`` to the red marker centroid."""
    image = Image.open(io.BytesIO(png)).convert('RGBA')
    pixels = [
        (x, y) for y in range(image.height) for x in range(image.width)
        if (lambda pixel: (
            pixel[0] > 180 and pixel[1] < 100 and
            pixel[2] < 100 and pixel[3] > 100))(image.getpixel((x, y)))]
    assert pixels
    center_x = sum(x for x, _ in pixels) / len(pixels)
    center_y = sum(y for _, y in pixels) / len(pixels)
    return math.atan2(center_y - anchor[1], center_x - anchor[0])


def assert_render_angle(svg, anchor, expected, tolerance=12):
    """Assert that a rendered marker points in the expected direction."""
    actual = red_centroid_angle(svg2png(svg), anchor)
    difference = math.degrees(
        (actual - expected + math.pi) % (2 * math.pi) - math.pi)
    assert abs(difference) < tolerance


def test_line_and_midpoint_orientations():
    """Lines use their tangent, and mid markers cross the +/-pi boundary."""
    vertices = path_vertices('M 90 30 L 50 20 L 10 30')
    assert [(vertex.point, vertex.position) for vertex in vertices] == [
        ((90, 30), 'start'), ((50, 20), 'mid'), ((10, 30), 'end')]
    assert_angle(vertices[0].angle, math.atan2(-10, -40))
    assert_angle(vertices[1].angle, math.pi)
    assert_angle(vertices[2].angle, math.atan2(10, -40))

    relative = angles('m 90 30 l -40 -10 l -40 10')
    for (relative_angle, _), (absolute_angle, _) in zip(relative, angles(
            'M 90 30 L 50 20 L 10 30')):
        assert_angle(relative_angle, absolute_angle)


def test_horizontal_and_vertical_commands():
    """Absolute and relative H/V commands keep their line directions."""
    absolute = angles('M 10 10 H 90 V 90 h -80 v -80')
    relative = angles('M 10 10 h 80 v 80 h -80 v -80')
    for (actual, _), (expected, _) in zip(relative, absolute):
        assert_angle(actual, expected)

    assert [position for _, position in absolute] == [
        'start', 'mid', 'mid', 'mid', 'end']
    assert_angle(absolute[0][0], 0)
    assert_angle(absolute[1][0], math.pi / 4)
    assert_angle(absolute[2][0], 3 * math.pi / 4)
    assert_angle(absolute[3][0], -3 * math.pi / 4)
    assert_angle(absolute[4][0], -math.pi / 2)


def test_curve_endpoint_orientations():
    """Cubic and quadratic markers use real endpoint tangents."""
    cubic = angles('M 10 80 C 20 10 80 10 90 70')
    assert_angle(cubic[0][0], math.atan2(-70, 10))
    assert_angle(cubic[-1][0], math.atan2(60, 10))

    quadratic = angles('M 10 50 Q 50 0 90 50')
    assert_angle(quadratic[0][0], math.atan2(-50, 40))
    assert_angle(quadratic[-1][0], math.atan2(50, 40))

    cubic_relative = angles('m 10 80 c 10 -70 70 -70 80 -10')
    for (actual, _), (expected, _) in zip(cubic_relative, cubic):
        assert_angle(actual, expected)


def test_smooth_continuations():
    """Reflected control points give continuous tangent directions."""
    cubic = angles('M 10 50 C 20 10 40 10 50 50 S 80 90 90 50')
    assert_angle(cubic[0][0], math.atan2(-40, 10))
    assert_angle(cubic[1][0], math.atan2(40, 10))
    assert_angle(cubic[2][0], math.atan2(-40, 10))

    cubic_relative = angles('m 10 50 c 10 -40 30 -40 40 0 s 30 40 40 0')
    for (actual, _), (expected, _) in zip(cubic_relative, cubic):
        assert_angle(actual, expected)

    quadratic = angles('M 10 50 Q 30 10 50 50 T 90 50')
    assert_angle(quadratic[0][0], math.atan2(-40, 20))
    assert_angle(quadratic[1][0], math.atan2(40, 20))
    assert_angle(quadratic[2][0], math.atan2(-40, 20))

    quadratic_relative = angles('m 10 50 q 20 -40 40 0 t 40 0')
    for (actual, _), (expected, _) in zip(quadratic_relative, quadratic):
        assert_angle(actual, expected)


def test_elliptical_arc_orientations():
    """Arcs use their actual ellipse tangent, including rotation and sweep."""
    arc = angles('M 10 50 A 40 40 0 0 1 90 50')
    assert_angle(arc[0][0], -math.pi / 2)
    assert_angle(arc[-1][0], math.pi / 2)

    reverse = angles('M 10 50 A 40 40 0 0 0 90 50')
    assert_angle(reverse[0][0], math.pi / 2)
    assert_angle(reverse[-1][0], -math.pi / 2)

    absolute = angles('M 10 50 A 40 20 30 0 1 90 50')
    relative = angles('m 10 50 a 40 20 30 0 1 80 0')
    assert absolute == relative
    assert_angle(absolute[0][0], math.radians(-126.58677555362947))
    assert_angle(absolute[-1][0], math.radians(53.41322444637053))


def test_closed_paths_and_subpaths():
    """Closing segments and multiple subpaths get their own markers."""
    vertices = path_vertices('M 10 10 L 90 10 L 90 90 Z')
    assert [(vertex.point, vertex.position) for vertex in vertices] == [
        ((10, 10), 'start'), ((90, 10), 'mid'), ((90, 90), 'mid'),
        ((10, 10), 'end')]
    assert_angle(vertices[0].angle, 0)
    assert_angle(vertices[1].angle, math.pi / 4)
    assert_angle(vertices[2].angle, math.radians(157.5))
    assert_angle(vertices[3].angle, math.radians(-135))

    vertices = path_vertices('M 10 10 L 90 10 M 10 30 L 90 30')
    assert [(vertex.point, vertex.position) for vertex in vertices] == [
        ((10, 10), 'start'), ((90, 10), 'end'),
        ((10, 30), 'start'), ((90, 30), 'end')]


def test_closepath_from_origin_is_rendered():
    """A closepath starting at (0, 0) still draws its closing segment."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">'
        '<path d="M 0 0 L 110 0 L 110 110 Z" fill="none" '
        'stroke="black" stroke-width="4"/></svg>').encode()
    image = Image.open(io.BytesIO(
        svg2png(svg, background_color='white'))).convert('RGB')
    assert any(
        sum(image.getpixel((x, y))) < 300
        for x in range(50, 60) for y in range(50, 60))


def test_basic_shapes_use_the_same_marker_geometry():
    """Lines, polylines and polygons get oriented markers too."""
    line = element_vertices('<line x1="10" y1="10" x2="90" y2="90"/>')
    assert [vertex.position for vertex in line] == ['start', 'end']
    assert_angle(line[0].angle, math.pi / 4)
    assert_angle(line[-1].angle, math.pi / 4)

    polyline = element_vertices(
        '<polyline points="90,30 50,20 10,30" fill="none"/>')
    assert [vertex.position for vertex in polyline] == ['start', 'mid', 'end']
    assert_angle(polyline[1].angle, math.pi)

    polygon = element_vertices(
        '<polygon points="10,10 90,10 90,90" fill="none"/>')
    assert [(vertex.point, vertex.position) for vertex in polygon] == [
        ((10, 10), 'start'), ((90, 10), 'mid'), ((90, 90), 'mid'),
        ((10, 10), 'end')]
    assert_angle(polygon[-1].angle, math.radians(-135))


@pytest.mark.parametrize('path', (
    'M 10 10',
    'M 10 10 L 10 10',
    'M 10 10 L 10 10 L 90 10',
    'M 10 10 L 90 10 L 90 10',
    'M 10 10 L 10 10 L 10 10 Z',
    'M 10 10 C 10 10 10 10 90 10',
    'M 10 10 C 10 10 90 10 90 10',
    'M 10 10 Q 10 10 90 10',
    'M 10 10 A 10 10 0 0 1 10 10',
    'M 10 10 L 10 10 M 90 90 L 90 90',
))
def test_degenerate_paths_are_stable(path):
    """Zero-length and fully degenerate paths keep valid marker vertices."""
    vertices = path_vertices(path)
    assert vertices
    assert all(math.isfinite(vertex.angle) for vertex in vertices)
    assert all(math.isfinite(value) for vertex in vertices
               for value in vertex.point)
    assert {vertex.position for vertex in vertices} <= {'start', 'mid', 'end'}


def test_coincident_control_points():
    """Coincident control points fall back to the next path direction."""
    cases = (
        ('M 10 80 C 10 80 50 20 90 80',
         math.atan2(-60, 40), math.atan2(60, 40)),
        ('M 10 80 C 50 20 90 80 90 80',
         math.atan2(-60, 40), math.atan2(60, 40)),
        ('M 10 80 C 10 80 90 80 90 80', 0, 0),
        ('M 10 80 Q 10 80 90 80', 0, 0),
        ('M 10 80 Q 90 80 90 80', 0, 0),
    )
    for path, start_expected, end_expected in cases:
        vertices = path_vertices(path)
        assert_angle(vertices[0].angle, start_expected)
        assert_angle(vertices[-1].angle, end_expected)


def test_png_marker_orientation():
    """PNG markers are oriented along curves and around the angle boundary."""
    cubic = render_marker('M 20 110 C 30 30 90 30 100 100')
    assert_render_angle(
        cubic, (20, 110), math.atan2(-80, 10))

    quadratic = render_marker(
        'M 20 110 Q 60 30 100 110', markers='marker-end="url(#arrow)"')
    assert_render_angle(
        quadratic, (100, 110), math.atan2(80, 40))

    fold = render_marker(
        'M 90 70 L 50 60 L 10 70', markers='marker-mid="url(#arrow)"')
    assert_render_angle(fold, (50, 60), math.pi)

    arc = render_marker('M 20 70 A 40 40 0 0 1 100 70')
    assert_render_angle(arc, (20, 70), -math.pi / 2)


def test_fixed_and_auto_start_reverse_orientation():
    """Fixed angles and auto-start-reverse keep their original meaning."""
    fixed = render_marker('M 20 70 L 100 70', orient='90')
    assert_render_angle(fixed, (20, 70), math.pi / 2)

    reverse = render_marker(
        'M 20 70 L 100 70', orient='auto-start-reverse')
    assert_render_angle(reverse, (20, 70), math.pi)


def test_vector_export_marker_orientation():
    """SVG vector export keeps the same marker direction as PNG output."""
    svg = render_marker(
        'M 90 70 L 50 60 L 10 70', markers='marker-mid="url(#arrow)"')
    vector = svg2svg(svg)
    assert vector.startswith(b'<?xml')
    assert_render_angle(vector, (50, 60), math.pi)
    assert svg2pdf(svg).startswith(b'%PDF')
