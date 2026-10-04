"""Pixel math for the Windows overlay, orb, and tray icon — pure numpy.

The GNOME overlay is near-black glass with an accent hairline and a soft
phosphor glow in the state's signal colour. GDI can't antialias a rounded
rectangle or draw a glow, so the shapes are rendered here as straight
(non-premultiplied) RGB + alpha layers from signed-distance fields; the
shell draws text on top with GDI, then premultiplies for
UpdateLayeredWindow. Kept free of any Win32 so it is testable anywhere.
"""

from dataclasses import dataclass

import numpy as np

INSTRUMENT_BG = (12, 12, 17)          # #0c0c11
TITLE_GREY = (138, 138, 149)          # #8a8a95
DETAIL_INK = (170, 170, 174)          # rgba(232,232,236,.72) over the glass
PILL_ALPHA = 0.965


@dataclass(frozen=True)
class StateStyle:
    label: str
    detail: str
    accent: tuple[int, int, int]
    glow: float
    active: bool = False


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


# Mirrors STATE_STYLES in the GNOME extension: colour lives in the signal.
STATE_STYLES = {
    "starting": StateStyle("STARTING", "Opening microphone", _hex("#22d3ee"), 0.30, True),
    "listening": StateStyle("LISTENING", "Press again to stop", _hex("#e5484d"), 0.32, True),
    "processing": StateStyle("PROCESSING", "Transcribing", _hex("#fbbf24"), 0.30, True),
    "inserted": StateStyle("INSERTED", "", _hex("#51cf66"), 0.28),
    "empty": StateStyle("NO SIGNAL", "Try again", _hex("#8a8a95"), 0.18),
    "error": StateStyle("ERROR", "", _hex("#a78bfa"), 0.30),
}

ORB_CYAN = _hex("#22d3ee")
ORB_CYAN_HOT = _hex("#7de9f7")

BAR_COUNT = 4
BAR_SLOT = 24
BAR_WAVE = (6, 10, 16, 22, 16, 10)
BAR_STATIC = (11, 17, 13, 8)


def style_for(state: str) -> StateStyle:
    return STATE_STYLES.get(state, STATE_STYLES["listening"])


def bar_heights(phase: int | None) -> list[int]:
    """Unscaled bar heights: the frozen skyline at rest, a staggered wave
    while active (phase advances on the animation tick)."""
    if phase is None:
        return list(BAR_STATIC)
    return [BAR_WAVE[(phase + i * 2) % len(BAR_WAVE)] for i in range(BAR_COUNT)]


def _rounded_rect_sdf(width, height, x0, y0, w, h, radius):
    """Signed distance (px; negative inside) to a rounded rectangle, sampled
    at pixel centres over a width x height canvas."""
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32) + 0.5
    cx, cy = x0 + w / 2.0, y0 + h / 2.0
    qx = np.abs(xs - cx) - (w / 2.0 - radius)
    qy = np.abs(ys - cy) - (h / 2.0 - radius)
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    return outside + inside - radius


def _compose(sdf, glow_sdf, *, fill, accent, border_px, glow_alpha, glow_sigma):
    """Fill + accent hairline + outer glow -> (rgb float HxWx3, alpha HxW)."""
    coverage = np.clip(0.5 - sdf, 0.0, 1.0)
    # Hairline: full accent within border_px of the edge, fading over 1 px.
    ring = np.clip(border_px + 0.5 - np.abs(sdf + border_px / 2.0), 0.0, 1.0)
    ring = np.minimum(ring, coverage)
    fill_rgb = np.empty(sdf.shape + (3,), np.float32)
    fill_rgb[...] = np.asarray(fill, np.float32)
    accent_rgb = np.asarray(accent, np.float32)
    fill_rgb = fill_rgb * (1.0 - ring[..., None]) + accent_rgb * ring[..., None]
    fill_alpha = PILL_ALPHA + (1.0 - PILL_ALPHA) * ring

    glow = glow_alpha * np.exp(-np.maximum(glow_sdf, 0.0) / glow_sigma)
    glow = np.where(glow < 1.5 / 255.0, 0.0, glow)
    inner_a = coverage * fill_alpha
    outer_a = (1.0 - coverage) * glow
    alpha = inner_a + outer_a
    safe = np.where(alpha > 0, alpha, 1.0)
    rgb = (fill_rgb * inner_a[..., None] + accent_rgb * outer_a[..., None]) / safe[..., None]
    return rgb, alpha.astype(np.float32)


def pill_layers(
    canvas_w: int,
    canvas_h: int,
    *,
    margin: int,
    width: int,
    height: int,
    scale: float,
    accent: tuple[int, int, int],
    glow_alpha: float,
    bars: list[int],
    bars_x: int,
):
    """The overlay pill at (margin, margin) inside a glow margin, with the
    level-meter bars painted in. Returns (rgb, alpha) float arrays."""
    radius = 12.0 * scale
    sdf = _rounded_rect_sdf(canvas_w, canvas_h, margin, margin, width, height, radius)
    # box-shadow: 0 10px 34px — the glow sits a little below the pill.
    glow_sdf = _rounded_rect_sdf(
        canvas_w, canvas_h, margin, margin + 4.0 * scale, width, height, radius
    )
    rgb, alpha = _compose(
        sdf,
        glow_sdf,
        fill=INSTRUMENT_BG,
        accent=accent,
        border_px=max(1.0, round(scale)),
        glow_alpha=glow_alpha,
        glow_sigma=6.5 * scale,
    )
    bar_w = max(2, round(3 * scale))
    gap = max(2, round(3 * scale))
    slot = BAR_SLOT * scale
    bottom = int(round(margin + (height + slot) / 2.0))
    accent_rgb = np.asarray(accent, np.float32)
    for i, h in enumerate(bars):
        bar_h = max(3, int(round(h * scale)))
        x0 = int(margin + bars_x + i * (bar_w + gap))
        rgb[bottom - bar_h : bottom, x0 : x0 + bar_w] = accent_rgb
    return rgb, alpha


def orb_layers(
    size: int, *, margin: int, scale: float, ring_rgb, glow_alpha: float, mark_rgb=None
):
    """The Kai orb: a glass disc, a 2px accent ring, a halo, and the level
    bars as its mark (drawn here, not as a font glyph: no font to lack)."""
    canvas = size + 2 * margin
    radius = size / 2.0
    sdf = _rounded_rect_sdf(canvas, canvas, margin, margin, size, size, radius)
    glow_sdf = _rounded_rect_sdf(
        canvas, canvas, margin, margin + 3.0 * scale, size, size, radius
    )
    rgb, alpha = _compose(
        sdf,
        glow_sdf,
        fill=INSTRUMENT_BG,
        accent=ring_rgb,
        border_px=max(2.0, round(2 * scale)),
        glow_alpha=glow_alpha,
        glow_sigma=6.0 * scale,
    )
    mark = np.asarray(mark_rgb if mark_rgb is not None else ring_rgb, np.float32)
    ys, xs = np.mgrid[0:canvas, 0:canvas].astype(np.float32) + 0.5
    bar_w, gap = 3.0 * scale, 2.6 * scale
    x0 = canvas / 2.0 - (4 * bar_w + 3 * gap) / 2.0
    base = canvas / 2.0 + 7.0 * scale
    for i, frac in enumerate((0.42, 0.75, 0.58, 0.32)):
        cx = x0 + i * (bar_w + gap) + bar_w / 2.0
        r = bar_w / 2.0
        top = base - frac * 20.0 * scale
        cy = np.clip(ys, top + r, base - r)
        cover = np.clip(0.5 - (np.hypot(xs - cx, ys - cy) - r), 0.0, 1.0)
        rgb = rgb * (1.0 - cover[..., None]) + mark * cover[..., None]
    return rgb, alpha


def to_bgra_premultiplied(rgb, alpha) -> np.ndarray:
    """Straight RGB + alpha -> premultiplied BGRA uint8 (UpdateLayeredWindow)."""
    out = np.empty(alpha.shape + (4,), np.uint8)
    a = np.clip(alpha, 0.0, 1.0)
    premul = np.clip(rgb, 0.0, 255.0) * a[..., None]
    out[..., 0] = np.round(premul[..., 2])
    out[..., 1] = np.round(premul[..., 1])
    out[..., 2] = np.round(premul[..., 0])
    out[..., 3] = np.round(a * 255.0)
    return out


# ---------------------------------------------------------------- tray icon

def icon_rgba(size: int, accent: tuple[int, int, int] = ORB_CYAN) -> np.ndarray:
    """The app icon: the overlay's four level bars, in the signal colour, on
    a rounded square of instrument glass. Straight RGBA uint8, size x size.
    The accent changes with state (cyan idle, red recording, amber setup)."""
    ss = 4  # supersample for clean edges at 16 px
    n = size * ss
    radius = n * 0.24
    sdf = _rounded_rect_sdf(n, n, 0, 0, n, n, radius)
    tile = np.clip(0.5 - sdf, 0.0, 1.0)
    rgb = np.empty((n, n, 3), np.float32)
    # A touch lighter than the overlay glass so it reads on a dark taskbar.
    rgb[...] = np.asarray((22, 22, 30), np.float32)
    # A one-sample-wide accent hairline just inside the tile edge.
    ring_w = max(ss, n * 0.045)
    ring = np.clip(ring_w + 0.5 - np.abs(sdf + ring_w / 2.0), 0.0, 1.0) * tile
    accent_rgb = np.asarray(accent, np.float32)
    rgb = rgb * (1.0 - 0.55 * ring[..., None]) + accent_rgb * (0.55 * ring[..., None])

    heights = (0.36, 0.62, 0.48, 0.26)
    bar_w = n * 0.12
    gap = n * 0.07
    total = 4 * bar_w + 3 * gap
    x_start = (n - total) / 2.0
    base = n * 0.76
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float32) + 0.5
    for i, frac in enumerate(heights):
        x0 = x_start + i * (bar_w + gap)
        top = base - frac * n
        cx = x0 + bar_w / 2.0
        r = bar_w / 2.0
        # A capsule: vertical segment [top+r, base-r] thickened by r.
        cy = np.clip(ys, top + r, base - r)
        d = np.hypot(xs - cx, ys - cy) - r
        bar = np.clip(0.5 - d, 0.0, 1.0)
        rgb = rgb * (1.0 - bar[..., None]) + accent_rgb * bar[..., None]

    # Downsample (box filter) colour weighted by coverage, and coverage.
    def down(a):
        return a.reshape(size, ss, size, ss, *a.shape[2:]).mean(axis=(1, 3))

    alpha = down(tile)
    color = down(rgb * tile[..., None]) / np.where(alpha > 0, alpha, 1.0)[..., None]
    out = np.zeros((size, size, 4), np.uint8)
    out[..., :3] = np.clip(np.round(color), 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(np.round(alpha * 255.0), 0, 255).astype(np.uint8)
    return out


def _png(rgba: np.ndarray) -> bytes:
    import struct
    import zlib

    h, w = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def ico_bytes(sizes=(16, 20, 24, 32, 40, 48, 64, 256), accent=ORB_CYAN) -> bytes:
    """A multi-resolution .ico (PNG-compressed entries, Vista+), used for
    the Start menu shortcut and the uninstall entry."""
    import struct

    images = [_png(icon_rgba(s, accent)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(sizes, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b"".join(images)


def place_pill(
    width: int,
    height: int,
    anchor: tuple[int, int] | None,
    window_rect: tuple[int, int, int, int] | None,
    work_area: tuple[int, int, int, int],
    *,
    scale: float = 1.0,
) -> tuple[int, int]:
    """Top-left for the pill, mirroring the GNOME extension: centred just
    ABOVE the caret (below it when there's no room); with no usable caret,
    low and centred in the focused window; always inside the work area."""
    gap = int(14 * scale)
    margin = int(18 * scale)
    left, top, right, bottom = work_area
    x = y = None
    if anchor is not None and anchor[0] >= 0 and anchor[1] >= 0:
        ax, ay = anchor
        near_window = window_rect is None or (
            window_rect[0] - 96 <= ax <= window_rect[2] + 96
            and window_rect[1] - 96 <= ay <= window_rect[3] + 96
        )
        if near_window:
            x = ax - width // 2
            # The caret anchor is its BOTTOM edge; clear the line above it.
            y = ay - int(22 * scale) - height - gap
            if y < top + margin:
                y = ay + gap
    if x is None:
        if window_rect is not None:
            wl, wt, wr, wb = window_rect
            x = (wl + wr) // 2 - width // 2
            y = wb - height - int(56 * scale)
        else:
            x = (left + right) // 2 - width // 2
            y = bottom - height - int(64 * scale)
    x = max(left + margin, min(x, right - width - margin))
    y = max(top + margin, min(y, bottom - height - margin))
    return int(x), int(y)
