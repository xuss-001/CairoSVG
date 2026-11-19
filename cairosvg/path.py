"""
Paths manager.

"""

from collections import deque
from math import cos, hypot, radians, sin
from re import finditer

from .bounding_box import calculate_bounding_box
from .helpers import (
    PATH_LETTERS, MarkerBuilder, clip_marker_box, node_format, normalize,
    point, point_angle, preserve_ratio, quadratic_points, rotate, size)
from .url import parse_url


def draw_markers(surface, node):
    """Draw the markers attached to a path ``node``."""
    if not getattr(node, 'vertices', None):
        return

    markers = {}
    common_marker = parse_url(node.get('marker', '')).fragment
    for position in ('start', 'mid', 'end'):
        attribute = f'marker-{position}'
        if attribute in node:
            markers[position] = parse_url(node[attribute]).fragment
        else:
            markers[position] = common_marker

    for vertex in node.vertices:
        point, angle, position = vertex

        # Draw marker (if a marker exists for 'position')
        marker = markers[position]
        if marker:
            marker_node = surface.markers.get(marker)
            if marker_node is None:
                continue

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

    marker_builder = MarkerBuilder()
    cubic_control = None
    quadratic_control = None

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

            # Find the tangents in user coordinates, including rotation and
            # the non-uniform scale used to draw the ellipse.
            start_tangent = rotate(
                -rx * sin(angle1), rx * cos(angle1) * radii_ratio, rotation)
            end_tangent = rotate(
                -rx * sin(angle2), rx * cos(angle2) * radii_ratio, rotation)
            if not sweep:
                start_tangent = -start_tangent[0], -start_tangent[1]
                end_tangent = -end_tangent[0], -end_tangent[1]
            new_point = current_point[0] + x3, current_point[1] + y3
            if hypot(
                    new_point[0] - current_point[0],
                    new_point[1] - current_point[1]) <= 1e-12:
                # SVG omits an arc whose endpoints are identical.
                start_tangent = end_tangent = None
            marker_builder.segment(
                current_point, new_point, start_tangent, end_tangent)

            # Draw the arc
            surface.context.save()
            surface.context.translate(x1, y1)
            surface.context.rotate(rotation)
            surface.context.scale(1, radii_ratio)
            arc(xc, yc, rx, angle1, angle2)
            surface.context.restore()
            current_point = new_point

        elif letter == 'c':
            # Relative curve
            x, y = current_point
            x1, y1, string = point(surface, string)
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            control1 = x + x1, y + y1
            control2 = x + x2, y + y2
            new_point = x + x3, y + y3
            marker_builder.cubic_to(
                current_point, control1, control2, new_point)
            cubic_control = control2
            surface.context.rel_curve_to(x1, y1, x2, y2, x3, y3)
            current_point = new_point

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
            control1 = x1, y1
            control2 = x2, y2
            new_point = x3, y3
            marker_builder.cubic_to(
                current_point, control1, control2, new_point)
            cubic_control = control2
            surface.context.curve_to(x1, y1, x2, y2, x3, y3)
            current_point = new_point

        elif letter == 'h':
            # Relative horizontal line
            x, string = (string + ' ').split(' ', 1)
            old_x, old_y = current_point
            x = size(surface, x, 'x')
            new_point = old_x + x, old_y
            marker_builder.line_to(current_point, new_point)
            surface.context.rel_line_to(x, 0)
            current_point = new_point

        elif letter == 'H':
            # Horizontal line
            x, string = (string + ' ').split(' ', 1)
            old_y = current_point[1]
            x = size(surface, x, 'x')
            new_point = x, old_y
            marker_builder.line_to(current_point, new_point)
            surface.context.line_to(x, old_y)
            current_point = new_point

        elif letter == 'l':
            # Relative straight line
            x, y, string = point(surface, string)
            new_point = current_point[0] + x, current_point[1] + y
            marker_builder.line_to(current_point, new_point)
            surface.context.rel_line_to(x, y)
            current_point = new_point

        elif letter == 'L':
            # Straight line
            x, y, string = point(surface, string)
            new_point = x, y
            marker_builder.line_to(current_point, new_point)
            surface.context.line_to(x, y)
            current_point = new_point

        elif letter == 'm':
            # Current point relative move
            x, y, string = point(surface, string)
            surface.context.rel_move_to(x, y)
            current_point = current_point[0] + x, current_point[1] + y
            marker_builder.move_to(current_point)

        elif letter == 'M':
            # Current point move
            x, y, string = point(surface, string)
            surface.context.move_to(x, y)
            current_point = x, y
            marker_builder.move_to(current_point)

        elif letter == 'q':
            # Relative quadratic curve
            x1, y1 = 0, 0
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            control = current_point[0] + x2, current_point[1] + y2
            new_point = current_point[0] + x3, current_point[1] + y3
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            surface.context.rel_curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            marker_builder.quadratic_to(current_point, control, new_point)
            quadratic_control = control
            current_point = new_point

        elif letter == 'Q':
            # Quadratic curve
            x1, y1 = current_point
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            control = x2, y2
            new_point = x3, y3
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            surface.context.curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            marker_builder.quadratic_to(current_point, control, new_point)
            quadratic_control = control
            current_point = new_point

        elif letter == 's':
            # Relative smooth curve
            x, y = current_point
            x1 = x3 - x2 if last_letter in 'csCS' else 0
            y1 = y3 - y2 if last_letter in 'csCS' else 0
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            control1 = (
                2 * x - cubic_control[0], 2 * y - cubic_control[1]
            ) if cubic_control is not None and last_letter in 'csCS' else (
                x, y)
            control2 = x + x2, y + y2
            new_point = x + x3, y + y3
            marker_builder.cubic_to(
                current_point, control1, control2, new_point)
            cubic_control = control2
            surface.context.rel_curve_to(x1, y1, x2, y2, x3, y3)
            current_point = new_point

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
            x1 = x3 + (x3 - x2) if last_letter in 'csCS' else x
            y1 = y3 + (y3 - y2) if last_letter in 'csCS' else y
            x2, y2, string = point(surface, string)
            x3, y3, string = point(surface, string)
            control1 = (
                2 * x - cubic_control[0], 2 * y - cubic_control[1]
            ) if cubic_control is not None and last_letter in 'csCS' else (
                x, y)
            control2 = x2, y2
            new_point = x3, y3
            marker_builder.cubic_to(
                current_point, control1, control2, new_point)
            cubic_control = control2
            surface.context.curve_to(x1, y1, x2, y2, x3, y3)
            current_point = new_point

        elif letter == 't':
            # Relative quadratic curve end
            if last_letter not in 'QqTt':
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
            control = (
                2 * current_point[0] - quadratic_control[0],
                2 * current_point[1] - quadratic_control[1]
            ) if quadratic_control is not None and last_letter in 'QqTt' else (
                current_point)
            new_point = current_point[0] + x3, current_point[1] + y3
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            marker_builder.quadratic_to(current_point, control, new_point)
            quadratic_control = control
            surface.context.rel_curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            current_point = new_point

        elif letter == 'T':
            # Quadratic curve end
            abs_x, abs_y = current_point
            if last_letter not in 'QqTt':
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
            control = (
                2 * abs_x - quadratic_control[0],
                2 * abs_y - quadratic_control[1]
            ) if quadratic_control is not None and last_letter in 'QqTt' else (
                current_point)
            new_point = x3, y3
            xq1, yq1, xq2, yq2, xq3, yq3 = quadratic_points(
                x1, y1, x2, y2, x3, y3)
            marker_builder.quadratic_to(current_point, control, new_point)
            quadratic_control = control
            surface.context.curve_to(xq1, yq1, xq2, yq2, xq3, yq3)
            current_point = new_point

        elif letter == 'v':
            # Relative vertical line
            y, string = (string + ' ').split(' ', 1)
            old_x, old_y = current_point
            y = size(surface, y, 'y')
            new_point = old_x, old_y + y
            marker_builder.line_to(current_point, new_point)
            surface.context.rel_line_to(0, y)
            current_point = new_point

        elif letter == 'V':
            # Vertical line
            y, string = (string + ' ').split(' ', 1)
            old_x, old_y = current_point
            y = size(surface, y, 'y')
            new_point = old_x, y
            marker_builder.line_to(current_point, new_point)
            surface.context.line_to(old_x, y)
            current_point = new_point

        elif letter in 'zZ':
            # End of path
            marker_builder.close(current_point)
            if first_path_point is not None:
                surface.context.close_path()
                current_point = first_path_point

        if string := string.strip():
            next_letter = {'m': 'l', 'M': 'L'}.get(letter, letter)
            matches.appendleft((next_letter, string))
        last_letter = letter

    node.vertices = marker_builder.vertices()
