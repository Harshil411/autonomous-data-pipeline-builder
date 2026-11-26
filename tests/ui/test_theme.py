"""Two looks, one stylesheet: Night (dark) and Day (light).

Both modes must define exactly the same set of colour variables, and every text colour
must stay readable on the surface it sits on (WCAG contrast), so switching the look can
never make a status unreadable.
"""

import re

import pytest

from app.graph import render_graph
from app.theme import THEMES, active_mode, css
from app.viewmodel import build_view

HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_both_modes_define_the_same_variables():
    assert set(THEMES) == {"dark", "light"}
    assert set(THEMES["dark"]) == set(THEMES["light"])


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("text, surface, minimum", [
    ("text", "bg", 7.0), ("text", "surface", 7.0),          # body text
    ("muted", "bg", 4.5), ("muted", "surface", 4.5),        # secondary text
    ("orange-text", "orange-soft", 4.5),                    # "being worked on"
    ("green-text", "green-soft", 4.5),                      # "passed"
    ("red-text", "red-soft", 4.5),                          # "failed"
    ("maroon-text", "bg", 4.5),                             # "waiting for you"
    ("btn-text", "btn-bg", 7.0),                            # the main button
    ("gate-text", "gate-1", 4.5), ("gate-text", "gate-2", 4.5),  # the approval gate node
])
def test_text_is_readable_on_its_surface(mode, text, surface, minimum):
    t = THEMES[mode]
    assert contrast(t[text], t[surface]) >= minimum, (mode, text, surface)


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_stylesheet_takes_every_colour_from_the_mode(mode):
    sheet = css(mode)
    declared, rules = sheet.split("/* ---- page", 1)
    for name, value in THEMES[mode].items():
        assert f"--{name}: {value};" in declared
    # below the variable block no rule hard-codes a colour, so neither mode can leak
    assert HEX.search(rules) is None


def test_the_look_is_dark_unless_the_browser_says_light():
    assert active_mode() == "dark"       # no browser in a unit test
    assert css("anything else") == css("dark")


def test_graph_carries_no_colours_of_its_own(recorded):
    # colour comes from the stylesheet's classes, so the same SVG works in both modes
    assert HEX.search(render_graph(build_view(recorded))) is None


def test_tab_icon_is_the_logo_drawn_to_stand_alone():
    # A browser tab cannot read the app's stylesheet, so the icon carries real colours,
    # but it must be the same drawing as the header logo.
    from app.components import LOGO, favicon

    icon = favicon()
    assert icon.startswith("<svg") and 'xmlns="http://www.w3.org/2000/svg"' in icon
    shape = re.search(r'<path d="([^"]+)"', LOGO).group(1)
    assert f'd="{shape}"' in icon
    assert "var(--" not in icon and "class=" not in icon
    assert len(HEX.findall(icon)) >= 2  # the maroon-to-orange accent, written out


def test_live_graph_styles_cannot_leak_onto_the_architecture_diagram():
    # Graphviz, which draws the diagram on the About page, also names its groups "graph",
    # "node" and "edge". Our live-graph rules must therefore start at our own <svg
    # class="graph"> element, or they outline that diagram's labels and thicken its arrows.
    rules = css("dark").split("/* ---- page", 1)[1]
    selectors = re.findall(r"(?<![\w-])(\S*)\.graph\b", rules)
    assert selectors and all(prefix.endswith("svg") for prefix in selectors)
