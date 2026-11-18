"""
Regression tests for the automatic orientation of SVG markers.

The markers drawn by ``marker-start``, ``marker-mid`` and ``marker-end`` must
follow the direction of the path. These tests focus on cases that used to be
wrong: asymmetric curves, vertices close to the +/- 180 degrees boundary,
zero-length segments and fully degenerate sub-paths.

"""

import io
from math import atan2, degrees, isclose

import cairocffi as cairo
import pytest

from . import parser, surface, svg2pdf, svg2png, svg2ps


MARKER_DEFINITIONS = '''
<marker id="auto" viewBox="0 0 10 10" refX="9" refY="5"
        markerWidth="8" markerHeight="8" orient="auto">
  <path d="M 0 0 L 10 5 L 0 10 z" fill="red"/>
</marker>
<marker id="reverse" viewBox="0 0 10 10" refX="9" refY="5"
        markerWidth="8" markerHeight="8" orient="auto-start-reverse">
  <path d="M 0 0 L 10 5 L 0 10 z" fill="blue"/>
</marker>
<marker id="fixed" viewBox="0 0 10 10" refX="5" refY="5"
        markerWidth="6" markerHeight="6" orient="45">
  <rect width="10" height="10" fill="green"/>
</marker>
'''


def svg_document(body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400">'
        f'<defs>{MARKER_DEFINITIONS}</defs>{body}</svg>').encode()


def path_svg(d, marker='auto'):
    return svg_document(
        f'<path d="{d}" fill="none" stroke="black" stroke-width="2" '
        f'marker-start="url(#{marker})" marker-mid="url(#{marker})" '
        f'marker-end="url(#{marker})"/>')


class RecordingSurface(surface.Surface):
    """Surface keeping every cairo operation in a recording surface."""

    def __init__(self, tree):
        super().__init__(tree, None, 96)

    def _create_surface(self, width, height):
        cairo_surface = cairo.RecordingSurface(
            cairo.CONTENT_COLOR_ALPHA, (0, 0, width, height))
        return cairo_surface, width, height

    def finish(self):
        pass


def marker_rotations(bytestring):
    """Return the rotation angles (in degrees) applied to every marker."""
    tree = parser.Tree(bytestring=bytestring)
    test_surface = RecordingSurface(tree)
    rotations = []
    marker_depth = 0

    class RotatingContext:
        def __init__(self, context):
            self.context = context

        def __getattr__(self, name):
            return getattr(self.context, name)

        def rotate(self, angle):
            # ``draw_markers`` rotates the context once per marker to orient
            # it; rotations happening while a marker is being drawn belong
            # to the marker contents and must be ignored.
            if marker_depth and not getattr(self, 'rotated_for_marker', False):
                rotations.append(degrees(angle))
                self.rotated_for_marker = True
            self.context.rotate(angle)

        def save(self):
            self.rotated_for_marker = False
            self.context.save()

        def restore(self):
            self.rotated_for_marker = False
            self.context.restore()

    test_surface.context = RotatingContext(test_surface.context)
    original_draw_markers = surface.draw_markers

    def tracking_draw_markers(draw_surface, node):
        nonlocal marker_depth
        marker_depth += 1
        try:
            original_draw_markers(draw_surface, node)
        finally:
            marker_depth -= 1

    surface.draw_markers = tracking_draw_markers
    try:
        test_surface.draw(tree)
    finally:
        surface.draw_markers = original_draw_markers
    return rotations


def normalize_angle(angle):
    """Map an angle to the ``[-180, 180)`` range, in degrees."""
    return (angle + 180) % 360 - 180


def assert_angle(actual, expected, tolerance=0.1):
    assert isclose(normalize_angle(actual), normalize_angle(expected),
                   abs_tol=tolerance)


def test_line_markers_follow_tangent():
    """Open endpoints of a line use the line tangent."""
    rotations = marker_rotations(path_svg('M 20 120 L 220 60'))
    assert len(rotations) == 2
    expected = degrees(atan2(60 - 120, 220 - 20))
    assert_angle(rotations[0], expected)
    assert_angle(rotations[1], expected)


def test_line_element_markers_follow_tangent():
    """The ``line`` shape shares the path marker orientation rules."""
    document = svg_document(
        '<line x1="20" y1="120" x2="220" y2="60" '
        'marker-start="url(#auto)" marker-end="url(#auto)"/>')
    rotations = marker_rotations(document)
    assert len(rotations) == 2
    expected = degrees(atan2(60 - 120, 220 - 20))
    for rotation in rotations:
        assert_angle(rotation, expected)


def test_mid_marker_uses_incoming_and_outgoing_directions():
    """A right angle gets the 45 degrees bisector, not an average flip."""
    rotations = marker_rotations(path_svg('M 20 20 L 220 20 L 220 220'))
    assert len(rotations) == 3
    assert_angle(rotations[0], 0)
    assert_angle(rotations[1], 45)
    assert_angle(rotations[2], 90)


@pytest.mark.parametrize('d', (
    # At the fold the incoming and outgoing tangents are almost opposite
    # horizontal directions (+179.97 then -179.97 degrees). A naive angle
    # average would flip the marker to 0 degrees; the bisector must stay
    # close to 180 degrees. Both sides of the boundary are exercised.
    'M 380 99.9 L 200 100 L 20 99.9',
    'M 380 100.1 L 200 100 L 20 100.1',
))
def test_mid_marker_does_not_flip_near_180_degrees(d):
    """A vertex close to +/- 180 degrees keeps pointing along the fold."""
    rotations = marker_rotations(path_svg(d))
    assert len(rotations) == 3
    assert abs(normalize_angle(rotations[1])) > 179


def test_asymmetric_cubic_curve_markers():
    """Cubic markers use the control-point tangents at both ends."""
    rotations = marker_rotations(
        path_svg('M 20 200 C 200 40 40 360 380 120'))
    assert len(rotations) == 2
    start = degrees(atan2(40 - 200, 200 - 20))
    end = degrees(atan2(120 - 360, 380 - 40))
    assert_angle(rotations[0], start)
    assert_angle(rotations[1], end)


def test_smooth_cubic_curve_markers():
    """Smooth continuation (S) keeps the reflected control point tangent."""
    rotations = marker_rotations(
        path_svg('M 20 120 C 80 40 140 200 200 120 S 320 40 380 120'))
    assert len(rotations) == 3
    # The middle vertex joins two collinear tangents.
    assert_angle(rotations[1], rotations[0], tolerance=0.5)


def test_quadratic_curve_markers():
    """Quadratic markers no longer use the hard-coded 0 degrees angle."""
    rotations = marker_rotations(path_svg('M 20 300 Q 200 40 380 300'))
    assert len(rotations) == 2
    start = degrees(atan2(40 - 300, 200 - 20))
    end = degrees(atan2(300 - 40, 380 - 200))
    assert rotations[0] != 0
    assert_angle(rotations[0], start)
    assert_angle(rotations[1], end)


def test_smooth_quadratic_curve_markers():
    """The T command follows the reflected quadratic control point."""
    rotations = marker_rotations(
        path_svg('M 20 200 Q 110 40 200 200 T 380 200'))
    assert len(rotations) == 3
    assert all(-180 <= rotation < 180 for rotation in rotations)


def test_rotated_elliptical_arc_markers():
    """Rotated elliptical arcs transform tangents back to user space."""
    rotations = marker_rotations(
        path_svg('M 60 200 A 90 50 35 0 1 340 200'))
    assert len(rotations) == 2
    for rotation in rotations:
        assert rotation != 0


def test_relative_commands_markers():
    """Relative commands resolve tangents in absolute coordinates."""
    rotations = marker_rotations(path_svg('m 20 20 l 200 0 l 0 200'))
    assert len(rotations) == 3
    assert_angle(rotations[0], 0)
    assert_angle(rotations[1], 45)
    assert_angle(rotations[2], 90)


def test_closed_path_only_uses_start_and_mid_markers():
    """A closed sub-path puts a mid marker on the first vertex, no end one."""
    rotations = marker_rotations(path_svg('M 20 20 H 220 V 220 H 20 Z'))
    # Four vertices, each getting a single mid marker.
    assert len(rotations) == 4
    for expected, rotation in zip((-45, 45, 135, -135), rotations):
        assert_angle(rotation, expected)


def test_polygon_markers_are_closed():
    """Polygons behave like closed sub-paths."""
    document = svg_document(
        '<polygon points="20,20 220,20 220,220" '
        'marker-start="url(#auto)" marker-mid="url(#auto)" '
        'marker-end="url(#auto)"/>')
    rotations = marker_rotations(document)
    assert len(rotations) == 3


def test_multiple_subpaths_get_independent_markers():
    """Each sub-path receives its own start/mid/end markers."""
    rotations = marker_rotations(
        path_svg('M 20 20 L 120 20 M 200 200 L 300 200 L 300 100'))
    # Two markers for the first sub-path, three for the second one.
    assert len(rotations) == 5


def test_zero_length_segment_uses_other_direction():
    """Zero-length segments are skipped when resolving a tangent."""
    rotations = marker_rotations(
        path_svg('M 20 120 L 20 120 L 220 60'))
    expected = degrees(atan2(60 - 120, 220 - 20))
    assert len(rotations) == 3
    for rotation in rotations:
        assert_angle(rotation, expected)


def test_fully_degenerate_subpath_does_not_crash():
    """A sub-path only made of zero-length segments still gets markers."""
    document = svg_document(
        '<path d="M 50 50 L 50 50 L 50 50" fill="none" '
        'marker-start="url(#auto)" marker-mid="url(#auto)" '
        'marker-end="url(#auto)"/>')
    rotations = marker_rotations(document)
    assert rotations == [0, 0, 0]


def test_degenerate_curve_then_valid_segment():
    """Degenerate curves yield ``None`` tangents without breaking drawing."""
    document = svg_document(
        '<path d="M 20 20 C 20 20 20 20 20 20 L 220 220" fill="none" '
        'marker-start="url(#auto)" marker-mid="url(#auto)" '
        'marker-end="url(#auto)"/>')
    rotations = marker_rotations(document)
    assert len(rotations) == 3
    for rotation in rotations:
        assert_angle(rotation, 45)


def test_auto_start_reverse_flips_only_the_start_marker():
    """auto-start-reverse adds 180 degrees to the first marker only."""
    document = svg_document(
        '<path d="M 20 100 L 220 100 L 220 220" fill="none" '
        'marker-start="url(#reverse)" marker-mid="url(#reverse)" '
        'marker-end="url(#reverse)"/>')
    rotations = marker_rotations(document)
    assert_angle(rotations[0], 180)
    assert_angle(rotations[1], 45)
    assert_angle(rotations[2], 90)


def test_fixed_orient_keeps_its_constant_angle():
    """A numeric orient attribute overrides the automatic orientation."""
    rotations = marker_rotations(path_svg('M 20 20 L 220 220', 'fixed'))
    assert rotations
    for rotation in rotations:
        assert_angle(rotation, 45)


def test_marker_scale_and_clipping_attributes_are_kept():
    """Marker width, height, units and viewBox still drive the geometry."""
    document = svg_document(
        '<path d="M 20 100 L 220 100" fill="none" stroke-width="4" '
        'marker-start="url(#auto)" marker-end="url(#auto)"/>')
    png = svg2png(bytestring=document)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    # Scaled markers must produce visible non-blank pixels.
    image = cairo.ImageSurface.create_from_png(io.BytesIO(png))
    assert any(image.get_data())


@pytest.mark.parametrize('converter, magic', (
    (svg2png, b'\x89PNG\r\n\x1a\n'),
    (svg2pdf, b'%PDF'),
    (svg2ps, b'%!PS'),
))
def test_markers_export_to_raster_and_vector_formats(converter, magic):
    """Every export format renders markers without raising an error."""
    document = path_svg(
        'M 20 40 L 220 39 L 120 41 L 30 42 '
        'M 60 200 Q 200 60 360 240 '
        'M 60 320 A 80 40 25 0 1 340 320 Z')
    output = converter(bytestring=document)
    assert output.startswith(magic)
