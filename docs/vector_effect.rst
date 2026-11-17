==================
Non-scaling stroke
==================

CairoSVG supports the ``vector-effect="non-scaling-stroke"`` property on the
``path``, ``line``, ``polyline`` and ``polygon`` elements when exporting to
PNG.

When this property is set, the geometry of the shape still follows the
transformations applied to the element and to its ancestor groups (rotation,
translation, non-uniform scaling, skew and nested viewports), but the stroke
is painted in the coordinate system of the root SVG viewport:

* the stroke width is neither enlarged nor shrunk by the transformations;
* the width stays the same in every direction, including under non-uniform
  scaling and skew transformations;
* dashed lines keep their dash lengths and gaps, and the stroke caps and
  joins keep their declared shape;
* the fill, clipping paths, masks, filters, gradients, patterns and opacity
  are not affected: fills are still transformed with the shape, and gradient
  or pattern strokes keep their position in device space.

Only the transformations defined inside the SVG document are neutralized for
the stroke. Changing the export ``scale`` (or the output size), as well as
the DPI-based conversion of physical units such as ``pt``, keeps its current
behavior: the whole image, including the non-scaling strokes, is scaled
accordingly.

Elements without the property, or with another ``vector-effect`` value, are
rendered exactly as before. The property can be set either as an XML
attribute or through the CSS ``style`` attribute::

    <line x1="10" y1="10" x2="90" y2="10"
          stroke="black" stroke-width="4"
          vector-effect="non-scaling-stroke"/>

    <line x1="10" y1="30" x2="90" y2="30"
          stroke="black" stroke-width="4"
          style="vector-effect: non-scaling-stroke"/>

``circle``, ``ellipse`` and ``rect`` currently ignore the property, and text
and markers are not affected.
