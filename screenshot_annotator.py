#!/usr/bin/env python3
import io
import math


def _font(size, bold=False):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    candidates = [
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        # Linux
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if bold else 'Regular'}.ttf",
        f"/usr/share/fonts/truetype/freefont/FreeSans{'Bold' if bold else ''}.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_arrow(draw, x1, y1, x2, y2, color, width=2):
    draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
    angle = math.atan2(y2 - y1, x2 - x1)
    head = 14
    spread = math.pi / 6
    for sign in (1, -1):
        ax = x2 - head * math.cos(angle - sign * spread)
        ay = y2 - head * math.sin(angle - sign * spread)
        draw.line([(x2, y2), (int(ax), int(ay))], fill=color, width=width)


def annotate_failure(screenshot_bytes, step_name, error_text, hint=None):
    """
    Add a red failure banner, border, and optional callout to a screenshot.

    hint (optional): dict with keys x, y, w, h (pixel region to highlight,
    relative to the original screenshot) and label (short callout text).

    Always returns bytes — original bytes if Pillow is unavailable or crashes.
    """
    if not screenshot_bytes:
        return screenshot_bytes

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return screenshot_bytes

    BANNER_H  = 60
    RED       = (220, 38, 38)
    RED_DARK  = (160, 20, 20)
    RED_LIGHT = (255, 200, 200)
    WHITE     = (255, 255, 255)
    BORDER    = 4

    try:
        img    = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")
        canvas = Image.new("RGB", (img.width, img.height + BANNER_H), RED)
        canvas.paste(img, (0, BANNER_H))
        draw = ImageDraw.Draw(canvas)

        title_font = _font(17, bold=True)
        body_font  = _font(13)

        draw.text((12, 8),  f"FAILED: {step_name}", fill=WHITE,     font=title_font)
        draw.text((12, 34), (error_text or "").strip()[:110],        fill=RED_LIGHT, font=body_font)

        # Red border around entire image
        draw.rectangle(
            [0, 0, canvas.width - 1, canvas.height - 1],
            outline=RED_DARK, width=BORDER,
        )

        # Optional callout: box + arrow + label
        if hint:
            rx = hint["x"]
            ry = hint["y"] + BANNER_H
            rw = hint["w"]
            rh = hint["h"]
            lbl = hint.get("label", "")

            # Highlight box
            draw.rectangle([rx - 3, ry - 3, rx + rw + 3, ry + rh + 3],
                           outline=RED, width=3)

            if lbl:
                # Place label pill; shift below if too close to right edge
                lx = rx + rw + 14
                ly = ry
                if lx + len(lbl) * 8 > canvas.width - 10:
                    lx, ly = rx, ry + rh + 10

                # Arrow from label pill to the highlight box
                _draw_arrow(
                    draw,
                    lx - 4, ly + 10,
                    rx + rw + 3, ry + rh // 2,
                    RED, width=2,
                )

                # Label pill background + text
                pill_w = max(len(lbl) * 8 + 10, 60)
                draw.rectangle(
                    [lx - 4, ly - 2, lx + pill_w, ly + 20],
                    fill=RED, outline=RED_DARK,
                )
                draw.text((lx, ly), lbl, fill=WHITE, font=body_font)

        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()

    except Exception:
        return screenshot_bytes