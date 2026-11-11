"""The stages the host can pick for a round, and a simple map of each for the kids.

The maps use libmelee's own stage numbers, so they match what a bot sees in
the game: x grows to the right, y grows upwards, and the main floor is at y = 0.
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

import melee
from melee import stages as geometry


# In the order the admin menu lists them.
STAGES = {
    "BATTLEFIELD": {
        "name": "Battlefield",
        "about": "A medium stage with three floating platforms.",
    },
    "FINAL_DESTINATION": {
        "name": "Final Destination",
        "about": "One long flat floor and no platforms. Nowhere to hide!",
    },
    "DREAMLAND": {
        "name": "Dream Land",
        "about": "A big stage with three platforms. The tree in the middle sometimes blows wind.",
    },
    "YOSHIS_STORY": {
        "name": "Yoshi's Story",
        "about": "The smallest stage, with three platforms. The ends of the floor slope down, "
                 "and a cloud drifts by under the edges.",
    },
    "FOUNTAIN_OF_DREAMS": {
        "name": "Fountain of Dreams",
        "about": "A small stage. The two side platforms slowly move up and down.",
    },
    "POKEMON_STADIUM": {
        "name": "Pokémon Stadium",
        "about": "A wide floor with two low platforms.",
    },
}
DEFAULT_STAGE = "BATTLEFIELD"


def stage_info(stage: str | None) -> dict | None:
    """What the pages show about a stage, or None if there is none to show."""
    if stage not in STAGES:
        return None
    return {"id": stage, **STAGES[stage], "image": f"/stages/{stage}.svg"}


# The drawing. Every map uses the same scale, so the kids can compare sizes.

LEFT, RIGHT, TOP, BOTTOM = -112, 112, 74, -46
NIGHT, SLATE, CREAM, CREAM_DARK, SAND, SAGE_DARK = (
    "#2c2b35", "#6b6977", "#e6dfcd", "#c9c1ad", "#c4a25a", "#47665a",
)
PLATFORM_THICKNESS = 2.5

# Right half of each stage, from the middle outwards: the top of the floor,
# then its underside back to the middle. Only the edges and floor height come
# from the game; the underside is just a picture.
OUTLINES = {
    "BATTLEFIELD": (
        [(0, 0), (68.4, 0)],
        [(68.4, -4), (62, -10), (54, -12), (46, -20), (34, -24), (24, -32), (12, -34), (0, -40)],
    ),
    "FINAL_DESTINATION": (
        [(0, 0), (85.57, 0)],
        [(85.57, -3), (78, -9), (60, -18), (40, -28), (24, -40), (12, -56), (0, -60)],
    ),
    "DREAMLAND": (
        [(0, 0), (77.27, 0)],
        [(77.27, -6), (74, -14), (70, -30), (66, -60), (0, -60)],
    ),
    "YOSHIS_STORY": (
        [(0, 0), (39, 0), (56, -3.5)],
        [(54, -8), (46, -16), (32, -22), (16, -26), (0, -27)],
    ),
    "FOUNTAIN_OF_DREAMS": (
        [(0, 0), (63.35, 0)],
        [(63.35, -3), (58, -7), (40, -10), (14, -12), (10, -60), (0, -60)],
    ),
    "POKEMON_STADIUM": (
        [(0, 0), (87.75, 0)],
        [(87.75, -6), (84, -10), (70, -16), (50, -20), (30, -22), (0, -22)],
    ),
}

# libmelee has no side platforms for Fountain of Dreams because they move.
# These are where they start; gamestate.fod_platforms has their live heights.
FOD_SIDE_PLATFORMS = ((20.0, -49.5, -21.0), (28.0, 21.0, 49.5))


def _sx(x: float) -> float:
    """Game x to picture x."""
    return round(x - LEFT, 1)


def _sy(y: float) -> float:
    """Game y to picture y, which grows downwards."""
    return round(TOP - y, 1)


def _points(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{_sx(x)},{_sy(y)}" for x, y in points)


def _label(x: float, y: float, text: str, anchor: str = "start") -> str:
    return (
        f'<text x="{_sx(x)}" y="{_sy(y)}" text-anchor="{anchor}" '
        f'font-family="ui-monospace, Menlo, monospace" font-size="6" fill="{CREAM_DARK}">{text}</text>'
    )


def _platform(height: float, left: float, right: float, moving: bool = False) -> str:
    style = (
        f'fill="{SAND}" fill-opacity="0.45" stroke="{SAND}" stroke-width="0.8" stroke-dasharray="2 1.5"'
        if moving else f'fill="{SAND}"'
    )
    return (
        f'<rect x="{_sx(left)}" y="{_sy(height)}" width="{round(right - left, 1)}" '
        f'height="{PLATFORM_THICKNESS}" {style}/>'
    )


def _up_and_down(x: float, height: float) -> str:
    """Small arrows above and below a platform that moves."""
    above, below = height + 3, height - PLATFORM_THICKNESS - 3
    arrows = [
        [(x - 2.5, above), (x + 2.5, above), (x, above + 3)],
        [(x - 2.5, below), (x + 2.5, below), (x, below - 3)],
    ]
    return "".join(f'<polygon points="{_points(arrow)}" fill="{SAND}"/>' for arrow in arrows)


def _platforms(stage: melee.Stage) -> list[tuple[float, float, float]]:
    found = [
        geometry.left_platform_position(stage),
        geometry.right_platform_position(stage),
        geometry.top_platform_position(stage),
    ]
    return [platform for platform in found if platform[0] is not None]


def _scenery(stage: str) -> list[str]:
    """Things in the background that help the kids recognise the stage."""
    if stage == "DREAMLAND":
        trunk = f'<rect x="{_sx(-7)}" y="{_sy(20)}" width="14" height="20" fill="{SAGE_DARK}"/>'
        leaves = [
            f'<circle cx="{_sx(x)}" cy="{_sy(y)}" r="{r}" fill="{SAGE_DARK}"/>'
            for x, y, r in ((0, 30, 14), (-13, 23, 10), (13, 23, 10))
        ]
        return [trunk, *leaves]
    if stage == "YOSHIS_STORY":
        return [
            f'<circle cx="{_sx(x)}" cy="{_sy(-14)}" r="{r}" fill="{CREAM_DARK}"/>'
            for x, r in ((60, 3), (65, 4), (70, 3))
        ]
    return []


def stage_svg(stage: str) -> str:
    """A map of the stage, as an SVG image."""
    floor, underside = OUTLINES[stage]
    half = floor + underside
    outline = half + [(-x, y) for x, y in reversed(half)]
    floor_line = [(-x, y) for x, y in reversed(floor)] + floor
    edge_x, edge_y = floor[-1]

    parts = [
        f'<rect width="{RIGHT - LEFT}" height="{TOP - BOTTOM}" fill="{NIGHT}"/>',
        *_scenery(stage),
        f'<polygon points="{_points(outline)}" fill="{SLATE}"/>',
        f'<polyline points="{_points(floor_line)}" fill="none" '
        f'stroke="{CREAM}" stroke-width="2"/>',
        _label(-edge_x - 3, edge_y - 2, f"x={-round(edge_x)}", "end"),
        _label(edge_x + 3, edge_y - 2, f"x={round(edge_x)}"),
    ]
    for height, left, right in _platforms(melee.Stage[stage]):
        parts.append(_platform(height, left, right))
        if right > 0:
            parts.append(_label(right + 2, height - 2, f"y={round(height)}"))
    if stage == "FOUNTAIN_OF_DREAMS":
        for height, left, right in FOD_SIDE_PLATFORMS:
            parts.append(_platform(height, left, right, moving=True))
            parts.append(_up_and_down((left + right) / 2, height))
            if right > 0:
                parts.append(_label(right + 2, height - 2, "moves"))

    name = STAGES[stage]["name"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {RIGHT - LEFT} {TOP - BOTTOM}" '
        f'role="img" aria-label={quoteattr(f"Map of {name}")}>' + "".join(parts) + "</svg>"
    )


SVGS = {stage: stage_svg(stage) for stage in STAGES}
