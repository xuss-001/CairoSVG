CairoSVG is an SVG converter based on Cairo. It can export SVG files to PDF,
EPS, PS, and PNG files.

* Free software: LGPL license
* For Python 3.10+
* Documentation: https://cairosvg.org/documentation/
* Changelog: https://github.com/Kozea/CairoSVG/releases
* Code, issues, tests: https://github.com/Kozea/CairoSVG
* Code of conduct: https://www.courtbouillon.org/code-of-conduct
* Professional support: https://www.courtbouillon.org
* Donation: https://opencollective.com/courtbouillon

Filter support
==============

CairoSVG supports a single ``feGaussianBlur`` filter primitive on the
``SourceGraphic`` when exporting to PNG. Filters can be applied to individual
shapes or groups::

    <filter id="soft-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur in="SourceGraphic" stdDeviation="3 1"/>
    </filter>

``stdDeviation`` accepts one or two non-negative numbers. The first value
controls the horizontal blur and the second controls the vertical blur; a zero
value disables blur on that axis. ``filterUnits`` and ``primitiveUnits`` can
use either ``objectBoundingBox`` or ``userSpaceOnUse``. The filter region is
clipped to its ``x``, ``y``, ``width`` and ``height`` bounds, while blur may
extend beyond the source graphic inside that region.

Built-in ``feOffset``, ``feFlood`` and ``feBlend`` behavior is unchanged.
Filter chains, named primitive results and other filter primitives are not
part of this Gaussian blur support.

CairoSVG has been created and developed by Kozea (https://kozea.fr).
Professional support, maintenance and community management is provided by
CourtBouillon (https://www.courtbouillon.org).

Copyrights are retained by their contributors, no copyright assignment is
required to contribute to CairoSVG. Unless explicitly stated otherwise, any
contribution intentionally submitted for inclusion is licensed under the LGPL 
license, without any additional terms or conditions. For full
authorship information, see the version control history.
