"""
Paths manager.

"""

from collections import deque
from math import atan2, cos, hypot, pi, radians, sin
from re import finditer

from .bounding_box import calculate_bounding_box
from .helpers import (
    PATH_LETTERS, clip_marker_box, node_format, normalize, point, point_angle,
    preserve_ratio, quadratic_points, rotate, size)
from .url import parse_url


CLOSED_SUBPATH = object()


class SegmentAngles(tuple):
    """Tangent direction pair ``(start_angle, end_angle)`` of one segment.

    A dedicated subclass lets :func:`draw_markers` distinguish angle pairs
    (whose values may be ``None`` for a zero-length segment) from the flat
    ``(x, y)`` vertex tuples.

    """


def tangent_angle(x1, y1, x2, y2):
    """Return the direction of the vector ``(x2 - x1, y2 - y1)``.

    Return ``None`` for a zero-length vector, whose direction is undefined.

    """
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return None
    return atan2(dy, dx)


def marker_angles(incoming, outgoing):
    """Return the orientation of a marker at an internal vertex.

    ``incoming`` and ``outgoing`` are the tangent directions (in radians) of
    the segments entering and leaving the vertex. The bisector is computed on
    the unit vectors instead of averaging the angles, so crossing the
    +/- pi boundary cannot flip the result. A missing direction (a
    zero-length segment) means the other direction is used, as mandated by
    the SVG specification.

    """
    if incoming is None:
        return outgoing
    if outgoing is None:
        return incoming
    return atan2(sin(incoming) + sin(outgoing),
                 cos(incoming) + cos(outgoing))


def draw_markers(surface, node):
    """Draw the markers attached to a path ``node``."""
    if not getattr(node, 'vertices', None):
        return

    # Split the flat vertex stream into sub-paths. Each sub-path is a tuple
    # ``(points, angles, closed)`` where ``points`` holds the vertices and
    # ``angles`` holds, for every segment between two consecutive vertices, a
    # pair ``(start_angle, end_angle)`` (``None`` when the tangent of a
    # zero-length segment is undefined).
    #
    # The stream uses ``None`` as a separator for a new (open) sub-path and
    # ``CLOSED_SUBPATH`` to close the current one. Closing segments end back
    # on the first vertex, which is therefore not stored a second time.
    subpaths = []
    points = []
    angles = []
    stream = deque(node.vertices)
    while stream:
        item = stream.popleft()
        if item is CLOSED_SUBPATH:
            if points:
                subpaths.append((points, angles, True))
            points, angles = [], []
            continue
        if item is None:
            if points:
                subpaths.append((points, angles, False))
            points, angles = [], []
            continue
        if isinstance(item, SegmentAngles):
            angles.append(item)
        else:
            points.append(item)
        if not stream:
            subpaths.append((points, angles, False))

    def first_tangent(index, boundary):
        """Find the first defined tangent around vertex ``index``."""
        step = 1 if boundary == 'start' else -1
        while 0 <= index < len(angles):
            angle = angles[index][0 if boundary == 'start' else 1]
            if angle is not None:
                return angle
            index += step
        return None

    markers = {}
    common_marker = parse_url(node.get('marker', '')).fragment
    for position in ('start', 'mid', 'end'):
        attribute = f'marker-{position}'
        if attribute in node:
            markers[position] = parse_url(node[attribute]).fragment
        else:
            markers[position] = common_marker

    # Compute the orientation of every marker before drawing them, following
    # https://www.w3.org/TR/SVG/painting.html#MarkerAlgorithm and the SVG 2
    # rules for zero-length segments and degenerate sub-paths.
    placements = []
    for points, angles, closed in subpaths:
        if not points:
            continue
        # Fully degenerate sub-path: every vertex gets the same (zero)
        # orientation, except for marker-start with auto-start-reverse.
        if not angles or all(pair == (None, None) for pair in angles):
            for index, vertex in enumerate(points):
                position = ('start' if index == 0 else
                            'end' if index == len(points) - 1 else 'mid')
                placements.append((vertex, 0, position))
            continue

        for index, vertex in enumerate(points):
            if index == 0:
                if closed:
                    # The first vertex of a closed sub-path joins the last
                    # segment to the first one and only gets a mid marker.
                    angle = marker_angles(
                        angles[-1][1], first_tangent(0, 'start'))
                    position = 'mid'
                else:
                    angle = first_tangent(0, 'start')
                    if angle is None:
                        angle = 0
                    position = 'start'
            elif index == len(points) - 1 and not closed:
                angle = first_tangent(len(angles) - 1, 'end')
                if angle is None:
                    angle = 0
                position = 'end'
            else:
                angle = marker_angles(
                    first_tangent(index - 1, 'end'),
                    first_tangent(index, 'start'))
                position = 'mid'
            placements.append((vertex, angle, position))

    for point, angle, position in placements:
        # Draw marker (if a marker exists for 'position')
        marker = markers[position]
        if not marker:
            continue
        marker_node = surface.markers.get(marker)

        # Calculate scale based on current stroke (if requested)
        if marker_node.get('markerUnits') == 'userSpaceOnUse':
            scale = 1
        else:
            scale = size(
                surface, surface.parent_node.get('stroke-width', '1'))

        # Calculate position, (additional) scale and clipping based on
        # marker properties
        viewbox = node_format(surface, marker_node)[2]
        if viewbox:
            scale_x, scale_y, translate_x, translate_y = preserve_ratio(
                surface, marker_node)
            clip_box = clip_marker_box(
                surface, marker_node, scale_x, scale_y)
        else:
            # Calculate sizes
            marker_width = size(surface,
                                marker_node.get('markerWidth', '3'), 'x')
            marker_height = size(surface,
                                 marker_node.get('markerHeight', '3'), 'y')
            bounding_box = calculate_bounding_box(surface, marker_node)

            # Calculate position and scale (preserve aspect ratio)
            translate_x = -size(surface, marker_node.get('refX', '0'), 'x')
            translate_y = -size(surface, marker_node.get('refY', '0'), 'y')
            scale_x = scale_y = min(
                marker_width / bounding_box[2],
                marker_height / bounding_box[3])

            # No clipping since viewbox is not present
            clip_box = None

        # Add extra path for marker
        temp_path = surface.context.copy_path()
        surface.context.new_path()

        # Override angle (if requested)
        node_angle = marker_node.get('orient', '0')
        if node_angle not in ('auto', 'auto-start-reverse'):
            angle = radians(float(node_angle))
        elif node_angle == 'auto-start-reverse' and position == 'start':
            angle += radians(180)

        # Draw marker path
        # See http://www.w3.org/TR/SVG/painting.html#MarkerAlgorithm
        for child in marker_node.children:
            surface.context.save()
            surface.context.translate(*point)
            surface.context.rotate(angle)
            surface.context.scale(scale)
            surface.context.scale(scale_x, scale_y)
            surface.context.translate(translate_x, translate_y)

            # Add clipping (if present and requested)
            overflow = marker_node.get('overflow', 'hidden')
            if clip_box and overflow in ('hidden', 'scroll'):
                surface.context.save()
                surface.context.rectangle(*clip_box)
                surface.context.restore()
                surface.context.clip()

            surface.draw(child)
            surface.context.restore()

        surface.context.append_path(temp_path)


def path(surface, node):
    """Draw a path ``node``."""
    string = node.get('d', '')

    node.vertices = deque()

    for letter in PATH_LETTERS:
        string = string.replace(letter, f' {letter} ')

    last_letter = None
    string = normalize(string)

    # Keep the current point because Cairo's get_current_point is not accurate
    # enough. See https://github.com/Kozea/CairoSVG/issues/111.
    if surface.context.has_current_point():
        current_point = surface.context.get_current_point()
    else:
        surface.context.move_to(0, 0)
        current_point = 0, 0

    matches = deque(match.groups() for match in finditer(
        f'([{PATH_LETTERS}])([^{PATH_LETTERS}]*)', string.strip()))
    while matches:
        letter, string = matches.popleft()
        string = string.strip()

        if last_letter in (None, 'z', 'Z') and letter not in 'mM':
            # After a closed sub-path a drawing command implicitly starts a
            # new sub-path at the current point.
            if not node.vertices or node.vertices[-1] != current_point:
                node.vertices.append(current_point)
            first_path_point = current_point

        if last_letter in (None, 'm', 'M', 'z', 'Z'):
            first_path_point = None
        if letter not in (None, 'm', 'M', 'z', 'Z') and (
                first_path_point is None):
            first_path_point = current_point

        if letter in 'aA':
            # Elliptic curve
            surface.context.set_tolerance(0.00001)
            x1, y1 = current_point
            rx, ry, string = point(surface, string)
            rotation, string = string.split(' ', 1)
            rotation = radians(float(rotation))

            # The large and sweep values are not always separated from the
            # following values. These flags can only be 0 or 1, so reading a
            # single digit suffices.
            large, string = string[0], string[1:].strip()
            sweep, string = string[0], string[1:].strip()

            # Retrieve end point and set remainder (before checking flags)
            x3, y3, string = point(surface, string)

            # Only allow 0 or 1 for flags
            large, sweep = int(large), int(sweep)
            if large not in (0, 1) or sweep not in (0, 1):
                continue
            large, sweep = bool(large), bool(sweep)

            if letter == 'A':
                # Absolute x3 and y3, convert to relative
                x3 -= x1
                y3 -= y1

            # rx=0 or ry=0 means straight line
            if not rx or not ry:
                if string := string.strip():
                    # As we replace the current operation by l, we must be sure
                    # that the next letter is set to the real current letter (a
                    # or A) in case it’s omitted
                    matches.appendleft((letter, string))
                matches.appendleft(('l', f'{x3} {y3}'))
                continue

            radii_ratio = ry / rx

            # Cancel the rotation of the second point
            xe, ye = rotate(x3, y3, -rotation)
            ye /= radii_ratio

            # Find the angle between the second point and the x axis
            angle = point_angle(0, 0, xe, ye)

            # Put the second point onto the x axis
            xe = hypot(xe, ye)
            ye = 0

            # Update the x radius if it is too small
            rx = max(rx, xe / 2)

            # Find one circle centre
            xc = xe / 2
            yc = (rx ** 2 - xc ** 2) ** .5

            # Choose between the two circles according to flags
            if not (large ^ sweep):
                yc = -yc

            # Define the arc sweep
            arc = (
                surface.context.arc if sweep else surface.context.arc_negative)

            # Put the second point and the center back to their positions
            xe, ye = rotate(xe, 0, angle)
            xc, yc = rotate(xc, yc, angle)

            # Find the drawing angles
            angle1 = point_angle(xc, yc, 0, 0)
            angle2 = point_angle(xc, yc, xe, ye)

            # Store the tangent angles. In the rotated and scaled frame used
            # to draw the arc, the tangent at angle theta is theta + pi / 2
            # for a positive sweep and theta - pi / 2 for a negative sweep.
            # Rotate and scale this vector back to the user coordinate
            # system before measuring its direction, as required for
            # non-circular radii and a non-zero x-axis-rotation.
            direction = 1 if sweep else -1

            def arc_tangent(theta):
                ux, uy = -direction * sin(theta), direction * cos(theta)
                uy *= radii_ratio
                return atan2(
                    ux * sin(rotation) + uy * cos(rotation),
                    ux * cos(rotation) - uy * sin(rotation))

            tangent1 = arc_tangent(angle1)
            tangent2 = arc_tangent(angle2)
            node.vertices.append(SegmentAngles((tangent1, tangent2)))

            # Draw the arc
            surface.context.save()
            surface.context.translate(x1, y1)
            surface.context.rotate(rotation)
            surface.context.scale(1, radii_ratio)
            arc(xc, yc, rx, angle1, angle2)
            surface.context.restore()
            current_point = current_point[0] + x3, current_point[1] + y3

        elif letter == 'c':
            # Relative curve
            x, y = current_point
            x1, y1, string = point(surface, string)
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            node.vertices.append(SegmentAngles((
                tangent_angle(x, y, x + x1, y + y1),
                tangent_angle(x + x2, y + y2, x + x3, y + y3))))
            surface.context.rel_curve_to(x1, y1, x2, y2, x3, y3)
            current_point = current_point[0] + x3, current_point[1] + y3

            # Save absolute values for x and y, useful if next letter is s or S
            x1 += x
            x2 += x
            x3 += x
            y1 += y
            y2 += y
            y3 += y

        elif letter == 'C':
            # Curve
            x1, y1, string = point(surface, string)
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            node.vertices.append(SegmentAngles((
                tangent_angle(*current_point, x1, y1),
                tangent_angle(x2, y2, x3, y3))))
            surface.context.curve_to(x1, y1, x2, y2, x3, y3)
            current_point = x3, y3

        elif letter == 'h':
            # Relative horizontal line
            x, string = (string + ' ').split(' ', 1)
            old_x, old_y = current_point
            x = size(surface, x, 'x')
            angle = tangent_angle(old_x, old_y, old_x + x, old_y)
            node.vertices.append(SegmentAngles((angle, angle)))
            surface.context.rel_line_to(x, 0)
            current_point = current_point[0] + x, current_point[1]

        elif letter == 'H':
            # Horizontal line
            x, string = (string + ' ').split(' ', 1)
            old_x, old_y = current_point
            x = size(surface, x, 'x')
            angle = tangent_angle(old_x, old_y, x, old_y)
            node.vertices.append(SegmentAngles((angle, angle)))
            surface.context.line_to(x, old_y)
            current_point = x, current_point[1]

        elif letter == 'l':
            # Relative straight line
            x, y, string = point(surface, string)
            old_x, old_y = current_point
            angle = tangent_angle(old_x, old_y, old_x + x, old_y + y)
            node.vertices.append(SegmentAngles((angle, angle)))
            surface.context.rel_line_to(x, y)
            current_point = current_point[0] + x, current_point[1] + y

        elif letter == 'L':
            # Straight line
            x, y, string = point(surface, string)
            old_x, old_y = current_point
            angle = tangent_angle(old_x, old_y, x, y)
            node.vertices.append(SegmentAngles((angle, angle)))
            surface.context.line_to(x, y)
            current_point = x, y

        elif letter == 'm':
            # Current point relative move
            x, y, string = point(surface, string)
            if last_letter and last_letter not in 'zZ':
                node.vertices.append(None)
            surface.context.rel_move_to(x, y)
            current_point = current_point[0] + x, current_point[1] + y

        elif letter == 'M':
            # Current point move
            x, y, string = point(surface, string)
            if last_letter and last_letter not in 'zZ':
                node.vertices.append(None)
            surface.context.move_to(x, y)
            current_point = x, y

        elif letter == 'q':
            # Relative quadratic curve
            x1, y1 = 0, 0
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            surface.context.rel_curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            node.vertices.append(SegmentAngles((
                tangent_angle(0, 0, x2, y2),
                tangent_angle(x2, y2, x3, y3))))
            current_point = current_point[0] + x3, current_point[1] + y3

        elif letter == 'Q':
            # Quadratic curve
            x1, y1 = current_point
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            surface.context.curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            node.vertices.append(SegmentAngles((
                tangent_angle(x1, y1, x2, y2),
                tangent_angle(x2, y2, x3, y3))))
            current_point = x3, y3

        elif letter == 's':
            # Relative smooth curve
            x, y = current_point
            smooth = last_letter in 'csCS' if last_letter else False
            x1 = x3 - x2 if smooth else 0
            y1 = y3 - y2 if smooth else 0
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            node.vertices.append(SegmentAngles((
                tangent_angle(x, y, x + x1, y + y1),
                tangent_angle(x + x2, y + y2, x + x3, y + y3))))
            surface.context.rel_curve_to(x1, y1, x2, y2, x3, y3)
            current_point = current_point[0] + x3, current_point[1] + y3

            # Save absolute values for x and y, useful if next letter is s or S
            x1 += x
            x2 += x
            x3 += x
            y1 += y
            y2 += y
            y3 += y

        elif letter == 'S':
            # Smooth curve
            x, y = current_point
            smooth = last_letter in 'csCS' if last_letter else False
            x1 = x3 + (x3 - x2) if smooth else x
            y1 = y3 + (y3 - y2) if smooth else y
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            node.vertices.append(SegmentAngles((
                tangent_angle(x, y, x1, y1),
                tangent_angle(x2, y2, x3, y3))))
            surface.context.curve_to(x1, y1, x2, y2, x3, y3)
            current_point = x3, y3

        elif letter == 't':
            # Relative quadratic curve end
            if not last_letter or last_letter not in 'QqTt':
                x2, y2, x3, y3 = 0, 0, 0, 0
            elif last_letter in 'QT':
                x2 -= x1
                y2 -= y1
                x3 -= x1
                y3 -= y1
            x2 = x3 - x2
            y2 = y3 - y2
            x1, y1 = 0, 0
            x3, y3, string = point(surface, string)
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            node.vertices.append(SegmentAngles((
                tangent_angle(0, 0, x2, y2),
                tangent_angle(x2, y2, x3, y3))))
            surface.context.rel_curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            current_point = current_point[0] + x3, current_point[1] + y3

        elif letter == 'T':
            # Quadratic curve end
            abs_x, abs_y = current_point
            if not last_letter or last_letter not in 'QqTt':
                x2, y2, x3, y3 = abs_x, abs_y, abs_x, abs_y
            elif last_letter in 'qt':
                x2 += abs_x
                y2 += abs_y
                x3 += abs_x
                y3 += abs_y
            x2 = abs_x + (x3 - x2)
            y2 = abs_y + (y3 - y2)
            x1, y1 = abs_x, abs_y
            x3, y3, string = point(surface, string)
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            node.vertices.append(SegmentAngles((
                tangent_angle(x1, y1, x2, y2),
                tangent_angle(x2, y2, x3, y3))))
            surface.context.curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            current_point = x3, y3

        elif letter == 'v':
            # Relative vertical line
            y, string = (string + ' ').split(' ', 1)
            old_x, old_y = current_point
            y = size(surface, y, 'y')
            angle = tangent_angle(old_x, old_y, old_x, old_y + y)
            node.vertices.append(SegmentAngles((angle, angle)))
            surface.context.rel_line_to(0, y)
            current_point = current_point[0], current_point[1] + y

        elif letter == 'V':
            # Vertical line
            y, string = (string + ' ').split(' ', 1)
            old_x, old_y = current_point
            y = size(surface, y, 'y')
            angle = tangent_angle(old_x, old_y, old_x, y)
            node.vertices.append(SegmentAngles((angle, angle)))
            surface.context.line_to(old_x, y)
            current_point = current_point[0], y

        elif letter in 'zZ' and first_path_point:
            # End of path
            # The implicit closing segment joins the current point back to
            # the first point of the sub-path. Its angles are followed by
            # ``CLOSED_SUBPATH``, which marks the sub-path as closed: no end
            # marker is drawn and the first vertex gets a mid marker. The
            # first vertex itself is not stored again.
            closing_angle = tangent_angle(
                *current_point, *first_path_point)
            if closing_angle is not None:
                node.vertices.append(
                    SegmentAngles((closing_angle, closing_angle)))
            node.vertices.append(CLOSED_SUBPATH)
            surface.context.close_path()
            current_point = first_path_point

        elif letter in 'zZ':
            # A z/Z command without an open sub-path is a no-op: there is no
            # segment to close and no marker placement to record.
            pass

        if letter not in 'zZ':
            node.vertices.append(current_point)

        if string := string.strip():
            next_letter = {'m': 'l', 'M': 'L'}.get(letter, letter)
            matches.appendleft((next_letter, string))
        last_letter = letter
