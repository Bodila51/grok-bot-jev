#!/usr/bin/env python3
"""Reference motion renderer (from the Farm presentation job): offline Pillow+numpy frames piped to ffmpeg.
Adapt LINES / title / end card / timing. Output: $OUT_NAME (default video.mp4)."""

import math
import os
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


OUT = Path(__file__).resolve().parent
W, H, FPS, FRAMES = 1920, 1080, 30, 300
def _find_font():
    cands = [os.environ.get("FONT_PATH", ""), "/usr/share/fonts/truetype/sand-box/google/Geist/Geist-VariableFont_wght.ttf"]
    try:
        cands.append(subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True, text=True).stdout)
    except Exception:
        pass
    cands += ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    return next(p for p in cands if p and os.path.exists(p))


FONT_PATH = _find_font()

LINES = [
    "Runs Codex seats on its own computer · Claude optional",
    "Models: GPT-6 Astra · GPT-6 Sol · GPT-6 Luna",
    "Jev decides: answer inline or send to the farm, and which seat",
    "One job per seat, isolated logins, no quota hopping",
    "Brings back compact results, not transcripts",
]
END = "Farm — more model capacity, less chat noise"
END_NAME = "Farm"
END_TAGLINE = "— more model capacity, less chat noise"


def clamp(x):
    return max(0.0, min(1.0, x))


def ease_out_cubic(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_out_expo(x):
    x = clamp(x)
    return 1.0 if x >= 1 else 1 - 2 ** (-10 * x)


def smooth(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def font(size, weight):
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_axes([weight])  # variable fonts only
    except Exception:
        pass
    return f


def text_asset(s, f, color):
    bounds = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), s, font=f, anchor="lt")
    width = math.ceil(bounds[2] - bounds[0]) + 4
    height = math.ceil(bounds[3] - bounds[1]) + 4
    im = Image.new("RGBA", (width, height))
    ImageDraw.Draw(im).text((-bounds[0] + 2, -bounds[1] + 1), s, font=f, fill=color, anchor="lt")
    return im


def paste_asset(frame, asset, x, y, opacity=1, crop_width=None):
    if opacity <= 0:
        return
    source = asset
    if crop_width is not None:
        source = source.crop((0, 0, min(source.width, max(1, int(crop_width))), source.height))
    if opacity < 0.999:
        source = source.copy()
        source.putalpha(source.getchannel("A").point(lambda v: int(v * opacity)))
    frame.paste(source, (round(x), round(y)), source)


def base_background():
    # Fine fixed grain keeps large dark areas from feeling flat or banded.
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    nx, ny = xx / W, yy / H
    halo_left = np.exp(-(((nx - 0.16) / 0.67) ** 2 + ((ny - 0.03) / 0.73) ** 2) * 2.3)
    halo_right = np.exp(-(((nx - 0.99) / 0.72) ** 2 + ((ny - 0.87) / 0.77) ** 2) * 2.1)
    rng = np.random.default_rng(3742)
    grain = rng.normal(0, 0.65, (H, W)).astype(np.float32)
    channels = [
        6 + 4 * halo_left + 5 * halo_right + grain,
        11 + 12 * halo_left + 3 * halo_right + grain,
        22 + 20 * halo_left + 15 * halo_right + grain,
    ]
    array = np.stack(channels, axis=2).clip(0, 255).astype(np.uint8)
    im = Image.fromarray(array, "RGB")
    return im


def glow_sprite(color, size=760, power=36):
    grid = np.linspace(-1, 1, 280, dtype=np.float32)
    yy, xx = np.meshgrid(grid, grid, indexing="ij")
    rad = xx * xx + yy * yy
    alpha = np.clip(1 - rad, 0, 1) ** 2.3 * power
    data = np.zeros((280, 280, 4), dtype=np.uint8)
    data[:, :, :3] = color
    data[:, :, 3] = alpha.astype(np.uint8)
    return Image.fromarray(data, "RGBA").resize((size, size), Image.Resampling.BILINEAR)


def card_asset():
    cw, ch = 1510, 855
    shadow = Image.new("RGBA", (cw, ch))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((56, 52, cw - 56, ch - 30), radius=40, fill=(0, 0, 0, 145))
    shadow = shadow.filter(ImageFilter.GaussianBlur(36))
    surface = Image.new("RGBA", (cw, ch))
    surface.alpha_composite(shadow)
    d = ImageDraw.Draw(surface)
    d.rounded_rectangle((38, 26, cw - 38, ch - 44), radius=34, fill=(16, 25, 39, 239), outline=(65, 88, 112, 105), width=2)
    # Edge highlight and a restrained cyan accent on the upper edge.
    d.line((81, 27, 520, 27), fill=(74, 173, 177, 110), width=2)
    return surface


def fade_rgba(asset, opacity):
    if opacity >= 0.999:
        return asset
    copy = asset.copy()
    copy.putalpha(copy.getchannel("A").point(lambda v: int(v * opacity)))
    return copy


def draw_orbit(d, t, amount):
    if amount <= 0:
        return
    cx, cy = 1463, 288
    c1 = tuple(round(v * amount) for v in (63, 91, 112))
    c2 = tuple(round(v * amount) for v in (50, 70, 91))
    d.arc((cx - 91, cy - 91, cx + 91, cy + 91), 218, 528, fill=c1, width=2)
    d.arc((cx - 68, cy - 68, cx + 68, cy + 68), 42, 277, fill=c2, width=2)
    d.arc((cx - 43, cy - 43, cx + 43, cy + 43), 145, 390, fill=c2, width=2)
    theta = 2 * math.pi * (t / 13) - 0.65
    px, py = cx + 91 * math.cos(theta), cy + 91 * math.sin(theta)
    d.ellipse((px - 4, py - 4, px + 4, py + 4), fill=(68, 190, 195))
    d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(83, 119, 143))


def make_frame(i, background, glow_a, glow_b, card, header, title, line_assets, end_name, tagline_assets, end_glow):
    t = i / FPS
    im = background.copy()
    gx1 = round(-252 + 32 * math.sin(t * 0.48))
    gy1 = round(-324 + 22 * math.cos(t * 0.41))
    gx2 = round(1420 + 32 * math.cos(t * 0.37))
    gy2 = round(632 + 27 * math.sin(t * 0.44))
    im.paste(glow_a, (gx1, gy1), glow_a)
    im.paste(glow_b, (gx2, gy2), glow_b)

    # The résumé finishes its exit at 7.8 s. The end copy starts later,
    # so the two statements never occupy the same frame.
    exit_progress = smooth((t - 7.34) / 0.46)
    stage_out = 1 - exit_progress
    bare_background = im.copy() if exit_progress > 0 else None
    card_in = 0.18 + 0.82 * ease_out_cubic(t / 0.78)
    if stage_out > 0:
        card_alpha = card_in
        x = round(205 + 4 * (1 - card_in))
        piece = fade_rgba(card, card_alpha)
        im.paste(piece, (x, 96), piece)

        d = ImageDraw.Draw(im)
        draw_orbit(d, t, card_alpha * ease_out_cubic((t - 0.8) / 0.9))

        h_in = 0.18 + 0.82 * ease_out_expo(t / 0.95)
        paste_asset(im, header, 346, 211 + 36 * (1 - h_in), h_in)
        sub_in = ease_out_cubic((t - 0.88) / 0.85)
        paste_asset(im, title, 352, 343 + 18 * (1 - sub_in), sub_in)

        divider = ease_out_cubic((t - 1.03) / 0.76)
        if divider > 0:
            d.line((350, 426, 350 + round(1218 * divider), 426), fill=(48, 65, 82), width=2)
            d.line((350, 426, 350 + round(126 * divider), 426), fill=(74, 178, 184), width=3)

        # A thin spine gives the skill lines the rhythm of a typed chat reply.
        line_reveal = ease_out_cubic((t - 1.63) / 4.9)
        if line_reveal > 0:
            d.line((355, 477, 355, 477 + round(360 * line_reveal)), fill=(38, 64, 77), width=2)
            d.line((355, 477, 355, 477 + round(360 * line_reveal * 0.78)), fill=(49, 118, 125), width=2)

        for n, (s, asset) in enumerate(zip(LINES, line_assets)):
            start = 1.66 + n * 0.96
            entry = ease_out_cubic((t - start) / 0.38)
            typ = ease_out_cubic((t - start - 0.08) / 0.84)
            opacity = entry
            if opacity <= 0:
                continue
            y = 467 + n * 82
            x = 400 + 18 * (1 - entry)
            cy = y + 22
            # Diamond bullet with a small bright center, all drawn as shapes.
            ic = (44 + round(40 * entry), 130 + round(63 * entry), 138 + round(58 * entry))
            radius = 10 * entry
            d.polygon(((355, cy - radius), (355 + radius, cy), (355, cy + radius), (355 - radius, cy)), outline=ic, width=2)
            if typ > 0.15:
                d.ellipse((352, cy - 3, 358, cy + 3), fill=(94, 219, 217))
            width = asset.width * typ
            paste_asset(im, asset, x, y + 9 * (1 - entry), opacity, width)
            if 0.03 < typ < 0.99:
                cursor_x = round(x + width + 5)
                cursor_alpha = 0.65 + 0.35 * math.sin(t * 20) ** 2
                cursor_color = tuple(round(v * cursor_alpha) for v in (139, 245, 230))
                d.rounded_rectangle((cursor_x, y + 2, cursor_x + 3, y + 44), radius=2, fill=cursor_color)
            elif typ >= 0.99 and n == 4 and t < 7.54 and math.sin(t * 10) > 0:
                cursor_x = round(x + asset.width + 5)
                d.rounded_rectangle((cursor_x, y + 2, cursor_x + 3, y + 44), radius=2, fill=(107, 202, 205))

    if exit_progress > 0 and stage_out > 0:
        # The slight blur and fade apply to the complete card, including its
        # decorative shapes, avoiding any elements that pop off at the end.
        im = Image.blend(bare_background, im.filter(ImageFilter.GaussianBlur(2.4 * exit_progress)), stage_out)

    # Start the finale only after the entire résumé is invisible.
    end_in = ease_out_cubic((t - 7.86) / 0.56)
    if t >= 7.86:
        veil = Image.new("RGB", (W, H), (7, 13, 25))
        im = Image.blend(im, veil, 0.83 * end_in)
        glow = fade_rgba(end_glow, end_in)
        im.paste(glow, (580, 55), glow)
        d = ImageDraw.Draw(im)
        name_in = ease_out_expo((t - 7.88) / 0.46)
        paste_asset(im, end_name, (W - end_name.width) / 2, 348 + 26 * (1 - name_in), name_in)
        for n, (asset, word_x) in enumerate(tagline_assets):
            word_in = ease_out_expo((t - 8.08 - n * 0.055) / 0.32)
            paste_asset(im, asset, word_x, 565 + 18 * (1 - word_in), word_in)
        rule = round(340 * ease_out_cubic((t - 8.35) / 0.55))
        if rule > 0:
            d.line((960 - rule, 680, 960 + rule, 680), fill=(35, 68, 81), width=2)
            d.line((960 - min(rule, 76), 680, 960 + min(rule, 76), 680), fill=(91, 209, 207), width=3)
            d.ellipse((955, 675, 965, 685), fill=(117, 227, 222))
    return im


def main():
    assert len(LINES) == 5
    assert END_NAME + " " + END_TAGLINE == END
    bg = base_background()
    glow_a = glow_sprite((41, 143, 165), power=34)
    glow_b = glow_sprite((74, 65, 153), size=780, power=34)
    card = card_asset()
    header = text_asset("Farm", font(123, 650), (238, 247, 247, 255))
    title = text_asset("Multi-model worker farm", font(43, 400), (149, 170, 187, 255))
    line_assets = [text_asset(s, font(40, 430), (223, 232, 240, 255)) for s in LINES]
    end_name = text_asset(END_NAME, font(132, 650), (238, 246, 246, 255))
    tagline_font = font(64, 540)
    tagline_full = text_asset(END_TAGLINE, tagline_font, (225, 236, 242, 255))
    tagline_words = END_TAGLINE.split(" ")
    tagline_left = (W - tagline_full.width) / 2
    tagline_assets = []
    # Segment one rendered line so every animated word retains the same
    # baseline, weight, kerning and final spacing as the complete statement.
    start_px = 0
    advance = 0.0
    for n, word in enumerate(tagline_words):
        advance += tagline_font.getlength(word + " ")
        end_px = round(advance) if n < len(tagline_words) - 1 else tagline_full.width
        tagline_assets.append((tagline_full.crop((start_px, 0, end_px, tagline_full.height)), tagline_left + start_px))
        start_px = end_px
    end_glow = glow_sprite((47, 142, 157), size=760, power=34)
    assert max(a.width for a in line_assets) < 1180
    assert tagline_full.width < 1580

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT / os.environ.get("OUT_NAME", "video.mp4")),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(FRAMES):
            frame = make_frame(i, bg, glow_a, glow_b, card, header, title, line_assets, end_name, tagline_assets, end_glow)
            proc.stdin.write(frame.tobytes())
            if os.environ.get("SAVE_SAMPLES") == "1" and i in (0, 42, 108, 196, 234, 270, 299):
                frame.save(OUT / f"sample_{i:03d}.png")
            if i % 30 == 0:
                print(f"rendered {i}/{FRAMES}", flush=True)
    finally:
        if proc.stdin:
            proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg encoding failed")
    print("rendered 300/300", flush=True)


if __name__ == "__main__":
    main()
