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

CairoSVG has been created and developed by Kozea (https://kozea.fr).
Professional support, maintenance and community management is provided by
CourtBouillon (https://www.courtbouillon.org).

Masking
-------

PNG output supports the SVG ``mask-type`` property. A mask explicitly set to
``alpha`` uses only the alpha channel of its content. A mask explicitly set to
``luminance`` uses both color luminance and alpha, so white is fully visible
and black is fully transparent. The property can be set either as an attribute
or in CSS. Masks without ``mask-type`` keep CairoSVG's historical alpha
behavior.

Copyrights are retained by their contributors, no copyright assignment is
required to contribute to CairoSVG. Unless explicitly stated otherwise, any
contribution intentionally submitted for inclusion is licensed under the LGPL 
license, without any additional terms or conditions. For full
authorship information, see the version control history.
