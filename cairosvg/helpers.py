"""
Surface helpers.

"""

import re
from collections import deque, namedtuple
from math import atan2, cos, hypot, isfinite, radians, sin, tan

from .surface import cairo
from .url import parse_url

UNITS = {
    'mm': 1 / 25.4,
    'cm': 1 / 2.54,
    'in': 1,
    'pt': 1 / 72,
    'pc': 1 / 6,
    'px': None,
}

PAINT_URL = re.compile(r'(url\(.+\)) *(.*)')
PATH_LETTERS = 'achlmqstvzACHLMQSTVZ'
RECT = re.compile(r'rect\( ?(.+?) ?\)')


class PointError(Exception):
    """Exception raised when parsing a point fails."""


def distance(x1, y1, x2, y2):
    """Get the distance between two points."""
    return hypot(x2 - x1, y2 - y1)


def paint(value):
    """Extract from value an uri and a color.

    See http://www.w3.org/TR/SVG/painting.html#SpecifyingPaint

    """
    if not value:
        return None, None

    value = value.strip()
    match = PAINT_URL.search(value)
    if match:
        source = parse_url(match.group(1)).fragment
        color = match.group(2) or None
    else:
        source = None
        color = value or None

    return (source, color)


def node_format(surface, node, reference=True):
    """Return ``(width, height, viewbox)`` of ``node``.

    If ``reference`` is ``True``, we can rely on surface size to resolve
    percentages.

    """
    reference_size = 'xy' if reference else (0, 0)
    width = size(surface, node.get('width', '100%'), reference_size[0])
    height = size(surface, node.get('height', '100%'), reference_size[1])
    viewbox = node.get('viewBox')
    if viewbox:
        viewbox = re.sub('[ \n\r\t,]+', ' ', viewbox)
        viewbox = tuple(float(position) for position in viewbox.split())
        width = width or viewbox[2]
        height = height or viewbox[3]
    return width, height, viewbox


def normalize(string):
    """Normalize a string corresponding to an array of various values."""
    string = string.replace('E', 'e')
    string = re.sub('(?<!e)-', ' -', string)
    string = re.sub('[ \n\r\t,]+', ' ', string)
    string = re.sub(r'(\.[0-9-]+)(?=\.)', r'\1 ', string)
    return string.strip()


def point(surface, string):
    """Return ``(x, y, trailing_text)`` from ``string``."""
    match = re.match('(.*?) (.*?)(?: |$)', string)
    if match:
        x, y = match.group(1, 2)
        string = string[match.end():]
        return (size(surface, x, 'x'), size(surface, y, 'y'), string)
    else:
        raise PointError


def point_angle(cx, cy, px, py):
    """Return angle between x axis and point knowing given center."""
    return atan2(py - cy, px - cx)


MarkerVertex = namedtuple('MarkerVertex', 'point angle position')
_MarkerSegment = namedtuple(
    '_MarkerSegment', 'start end outgoing incoming')
_EPSILON = 1e-12


def _unit_vector(vector):
    """Return ``vector`` with a length of one, or ``None`` if it is zero."""
    if vector is None:
        return None
    x, y = vector
    length = hypot(x, y)
    if not isfinite(length) or length <= _EPSILON:
        return None
    return x / length, y / length


def _direction(start, end):
    """Return the unit vector going from ``start`` to ``end``."""
    return _unit_vector((end[0] - start[0], end[1] - start[1]))


def _first_direction(start, points):
    """Return the first non-zero direction from ``start`` to ``points``."""
    for end in points:
        direction = _direction(start, end)
        if direction is not None:
            return direction
    return None


def _first_incoming_direction(end, points):
    """Return the first non-zero direction from ``points`` to ``end``."""
    for start in points:
        direction = _direction(start, end)
        if direction is not None:
            return direction
    return None


def _angle(vector):
    """Return the angle of a vector, in radians."""
    return atan2(vector[1], vector[0])


def _marker_angle(incoming, outgoing):
    """Return the auto orientation for a marker vertex.

    Incoming and outgoing directions are travel directions.  Adding their
    unit vectors gives the angle bisector without wrapping around at +/-pi.
    """
    if incoming is not None and outgoing is not None:
        bisector = _unit_vector((
            incoming[0] + outgoing[0], incoming[1] + outgoing[1]))
        if bisector is not None:
            return _angle(bisector)
        # Exactly opposite directions have no geometric bisector.  Keep the
        # marker going forward along the outgoing segment instead of letting
        # rounding decide between two opposite orientations.
        return _angle(outgoing)
    if outgoing is not None:
        return _angle(outgoing)
    if incoming is not None:
        return _angle(incoming)
    return 0


class _MarkerSubpath:
    """Geometry of one path subpath, used to place its markers."""

    def __init__(self, start):
        self.start = start
        self.end = start
        self.segments = []
        self.closed = False


class MarkerBuilder:
    """Collect path geometry and build marker vertices from it."""

    def __init__(self):
        self.subpaths = []
        self.current = None

    def move_to(self, point):
        """Start a new subpath at ``point``."""
        self.current = _MarkerSubpath(point)
        self.subpaths.append(self.current)

    def _open_subpath(self, point):
        if self.current is None or self.current.closed:
            self.move_to(point)

    def segment(self, start, end, outgoing=None, incoming=None):
        """Add a segment with optional raw endpoint tangent vectors."""
        self._open_subpath(start)
        self.current.segments.append(_MarkerSegment(
            start, end, _unit_vector(outgoing), _unit_vector(incoming)))
        self.current.end = end

    def line_to(self, start, end):
        """Add a straight segment."""
        direction = _direction(start, end)
        self.segment(start, end, direction, direction)

    def cubic_to(self, start, control1, control2, end):
        """Add a cubic Bézier segment."""
        self.segment(
            start, end,
            _first_direction(start, (control1, control2, end)),
            _first_incoming_direction(
                end, (control2, control1, start)))

    def quadratic_to(self, start, control, end):
        """Add a quadratic Bézier segment."""
        self.segment(
            start, end,
            _first_direction(start, (control, end)),
            _first_incoming_direction(end, (control, start)))

    def close(self, point=None):
        """Close the current subpath with a straight segment."""
        if self.current is None:
            self.move_to(point or (0, 0))
        if self.current.closed:
            return
        point = self.current.end if point is None else point
        direction = _direction(point, self.current.start)
        self.current.segments.append(_MarkerSegment(
            point, self.current.start, direction, direction))
        self.current.end = self.current.start
        self.current.closed = True

    def _subpath_vertices(self, subpath):
        segments = subpath.segments
        if not segments:
            return [
                MarkerVertex(subpath.start, 0, 'start'),
                MarkerVertex(subpath.start, 0, 'end')]

        incoming = [segment.incoming for segment in segments]
        outgoing = [segment.outgoing for segment in segments]
        last = len(segments) - 1

        # Cache the nearest non-degenerate direction on each side of every
        # vertex, so that paths with many zero-length segments stay linear.
        incoming_before = []
        direction = None
        for segment_direction in incoming:
            direction = segment_direction or direction
            incoming_before.append(direction)
        outgoing_after = [None] * len(segments)
        direction = None
        for index in range(last, -1, -1):
            direction = outgoing[index] or direction
            outgoing_after[index] = direction

        vertices = []

        start_direction = (
            outgoing_after[0] or incoming_before[last])
        vertices.append(MarkerVertex(
            subpath.start, _marker_angle(None, start_direction), 'start'))

        for index in range(1, len(segments)):
            vertices.append(MarkerVertex(
                segments[index].start,
                _marker_angle(
                    incoming_before[index - 1], outgoing_after[index]),
                'mid'))

        if subpath.closed:
            end_point = subpath.start
            end_direction = (
                incoming_before[last] or outgoing_after[0])
        else:
            end_point = segments[-1].end
            end_direction = incoming_before[last]
        vertices.append(MarkerVertex(
            end_point, _marker_angle(end_direction, None), 'end'))
        return vertices

    def vertices(self):
        """Return all marker vertices as a deque of named tuples."""
        vertices = []
        for subpath in self.subpaths:
            vertices.extend(self._subpath_vertices(subpath))
        return deque(vertices)


def preserve_ratio(surface, node, width=None, height=None):
    """Manage the ratio preservation."""
    if node.tag == 'marker':
        width = width or size(surface, node.get('markerWidth', '3'), 'x')
        height = height or size(surface, node.get('markerHeight', '3'), 'y')
        _, _, viewbox = node_format(surface, node)
        viewbox_width, viewbox_height = viewbox[2:]
    elif node.tag in ('svg', 'image', 'g'):
        node_width, node_height, _ = node_format(surface, node)
        width = width or node_width
        height = height or node_height
        viewbox_width, viewbox_height = node.image_width, node.image_height
    else:
        raise TypeError(
            f'Root node is {node.tag}. Should be one of '
            'marker, svg, image, or g.'
        )

    translate_x = 0
    translate_y = 0
    scale_x = width / viewbox_width if viewbox_width > 0 else 1
    scale_y = height / viewbox_height if viewbox_height > 0 else 1

    aspect_ratio = node.get('preserveAspectRatio', 'xMidYMid').split()
    align = aspect_ratio[0]
    if align == 'none':
        x_position = 'min'
        y_position = 'min'
    else:
        meet_or_slice = aspect_ratio[1] if len(aspect_ratio) > 1 else None
        if meet_or_slice == 'slice':
            scale_value = max(scale_x, scale_y)
        else:
            scale_value = min(scale_x, scale_y)
        scale_x = scale_y = scale_value
        x_position = align[1:4].lower()
        y_position = align[5:].lower()

    if node.tag == 'marker':
        translate_x = -size(surface, node.get('refX', '0'), 'x')
        translate_y = -size(surface, node.get('refY', '0'), 'y')
    else:
        translate_x = 0
        if x_position == 'mid':
            translate_x = (width / scale_x - viewbox_width) / 2
        elif x_position == 'max':
            translate_x = width / scale_x - viewbox_width

        translate_y = 0
        if y_position == 'mid':
            translate_y += (height / scale_y - viewbox_height) / 2
        elif y_position == 'max':
            translate_y += height / scale_y - viewbox_height

    return scale_x, scale_y, translate_x, translate_y


def clip_marker_box(surface, node, scale_x, scale_y):
    """Get the clip ``(x, y, width, height)`` of the marker box."""
    width = size(surface, node.get('markerWidth', '3'), 'x')
    height = size(surface, node.get('markerHeight', '3'), 'y')
    _, _, viewbox = node_format(surface, node)
    viewbox_width, viewbox_height = viewbox[2:]

    align = node.get('preserveAspectRatio', 'xMidYMid').split(' ')[0]
    x_position = 'min' if align == 'none' else align[1:4].lower()
    y_position = 'min' if align == 'none' else align[5:].lower()

    clip_x = viewbox[0]
    if x_position == 'mid':
        clip_x += (viewbox_width - width / scale_x) / 2.
    elif x_position == 'max':
        clip_x += viewbox_width - width / scale_x

    clip_y = viewbox[1]
    if y_position == 'mid':
        clip_y += (viewbox_height - height / scale_y) / 2.
    elif y_position == 'max':
        clip_y += viewbox_height - height / scale_y

    return clip_x, clip_y, width / scale_x, height / scale_y


def quadratic_points(x1, y1, x2, y2, x3, y3):
    """Return the quadratic points to create quadratic curves."""
    xq1 = x2 * 2 / 3 + x1 / 3
    yq1 = y2 * 2 / 3 + y1 / 3
    xq2 = x2 * 2 / 3 + x3 / 3
    yq2 = y2 * 2 / 3 + y3 / 3
    return xq1, yq1, xq2, yq2, x3, y3


def rotate(x, y, angle):
    """Rotate a point of an angle around the origin point."""
    return x * cos(angle) - y * sin(angle), y * cos(angle) + x * sin(angle)


def transform(surface, transform_string, gradient=None, transform_origin=None):
    """Transform ``surface`` or ``gradient`` if supplied using ``string``.

    See http://www.w3.org/TR/SVG/coords.html#TransformAttribute

    """
    if not transform_string:
        return

    transformations = re.findall(
        r'(\w+) ?\( ?(.*?) ?\)', normalize(transform_string))
    matrix = cairo.Matrix()

    if transform_origin:
        origin = transform_origin.split(' ')
        origin_x = origin[0]
        if len(origin) == 1:
            if origin_x in ('top', 'bottom'):
                origin_y = origin_x
                origin_x = surface.width / 2
            else:
                origin_y = surface.height / 2
        elif len(origin) > 1:
            if origin_x in ('top', 'bottom'):
                origin_y = origin_x
                origin_x = origin[1]
            else:
                origin_y = origin[1]
        else:
            return

        if origin_x == 'center':
            origin_x = surface.width / 2
        elif origin_x == 'left':
            origin_x = 0
        elif origin_x == 'right':
            origin_x = surface.width
        else:
            origin_x = size(surface, origin_x, 'x')

        if origin_y == 'center':
            origin_y = surface.height / 2
        elif origin_y == 'top':
            origin_y = 0
        elif origin_y == 'bottom':
            origin_y = surface.height
        else:
            origin_y = size(surface, origin_y, 'y')

        matrix.translate(float(origin_x), float(origin_y))

    for transformation_type, transformation in transformations:
        values = [size(surface, value) for value in transformation.split(' ')]
        if transformation_type == 'matrix':
            matrix = cairo.Matrix(*values).multiply(matrix)
        elif transformation_type == 'rotate':
            angle = radians(float(values.pop(0)))
            x, y = values or (0, 0)
            matrix.translate(x, y)
            matrix.rotate(angle)
            matrix.translate(-x, -y)
        elif transformation_type == 'skewX':
            tangent = tan(radians(float(values[0])))
            matrix = cairo.Matrix(1, 0, tangent, 1, 0, 0).multiply(matrix)
        elif transformation_type == 'skewY':
            tangent = tan(radians(float(values[0])))
            matrix = cairo.Matrix(1, tangent, 0, 1, 0, 0).multiply(matrix)
        elif transformation_type == 'translate':
            if len(values) == 1:
                values += (0,)
            matrix.translate(*values[:2])
        elif transformation_type == 'scale':
            if len(values) == 1:
                values = 2 * values
            matrix.scale(*values[:2])

    if transform_origin:
        matrix.translate(-float(origin_x), -float(origin_y))

    try:
        matrix.invert()
    except cairo.Error:
        # Matrix not invertible, clip the surface to an empty path
        active_path = surface.context.copy_path()
        surface.context.new_path()
        surface.context.clip()
        surface.context.append_path(active_path)
    else:
        if gradient:
            # When applied on gradient use already inverted matrix (mapping
            # from user space to gradient space)
            matrix_now = gradient.get_matrix()
            gradient.set_matrix(matrix_now.multiply(matrix))
        else:
            matrix.invert()
            surface.context.transform(matrix)


def clip_rect(string):
    """Parse the rect value of a clip."""
    match = RECT.search(normalize(string or ''))
    return match.group(1).split(' ') if match else []


def rotations(node):
    """Retrieves the original rotations of a `text` or `tspan` node."""
    if 'rotate' in node:
        original_rotate = [
            float(i) for i in normalize(node['rotate']).strip().split(' ')]
        return original_rotate
    return []


def pop_rotation(node, original_rotate, rotate):
    """Removes the rotations of a node that are already used."""
    node['rotate'] = ' '.join(
        str(rotate.pop(0) if rotate else original_rotate[-1])
        for i in range(len(node.text)))


def zip_letters(xl, yl, dxl, dyl, rl, word):
    """Returns a list with the current letter's positions (x, y and rotation).

    E.g.: for letter 'L' with positions x = 10, y = 20 and rotation = 30:
    >>> [[10, 20, 30], 'L']

    Store the last value of each position and pop the first one in order to
    avoid setting an x,y or rotation value that have already been used.

    """
    return (
        ([pl.pop(0) if pl else None for pl in (xl, yl, dxl, dyl, rl)], char)
        for char in word)


def flatten(node):
    """Flatten the text of a node and its children."""
    flattened_text = [node.text or '']
    for child in list(node):
        flattened_text.append(flatten(child))
        flattened_text.append(child.tail or '')
        node.remove(child)
    return ''.join(flattened_text)


def size(surface, string, reference='xy'):
    """Replace a ``string`` with units by a float value.

    If ``reference`` is a float, it is used as reference for percentages. If it
    is ``'x'``, we use the viewport width as reference. If it is ``'y'``, we
    use the viewport height as reference. If it is ``'xy'``, we use
    ``hypot(viewport_width, viewport_height) / 2 ** .5`` as reference.

    """
    if not string:
        return 0

    try:
        return float(string)
    except ValueError:
        # Not a float, try something else
        pass

    # No surface (for parsing only)
    if surface is None:
        return 0

    string = normalize(string).split(' ', 1)[0]
    if string.endswith('%'):
        if reference == 'x':
            reference = surface.context_width or 0
        elif reference == 'y':
            reference = surface.context_height or 0
        elif reference == 'xy':
            reference = (
                hypot(surface.context_width, surface.context_height) / 2 ** .5
            )
        return float(string[:-1]) * reference / 100
    elif string.endswith('em'):
        return surface.font_size * float(string[:-2])
    elif string.endswith('ex'):
        # Assume that 1em == 2ex
        return surface.font_size * float(string[:-2]) / 2
    elif string.endswith('ch'):
        # A '0' must be assumed to be 0.5em wide.
        return surface.font_size * float(string[:-2]) / 2

    for unit, coefficient in UNITS.items():
        if string.endswith(unit):
            number = float(string[:-len(unit)])
            return number * (surface.dpi * coefficient if coefficient else 1)

    # Unknown size
    return 0
