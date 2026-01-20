from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ImageRenderConfig:
    enabled: bool = False
    cache_images: bool = False
    keep_images: int = 10


def _safe_filename(stem: str) -> str:
    return ''.join(c for c in stem if c.isalnum() or c in ('-', '_'))[:64] or 'wf'


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()


def render_text_to_png_bytes(text: str, *, title: str = 'Warframe', width: int = 900) -> bytes:
    # Pillow-based, no browser rendering.
    from PIL import Image, ImageDraw, ImageFont

    pad = 20
    line_gap = 6

    font = ImageFont.load_default()

    lines = [title, ''] + (text.splitlines() if text else ['(empty)'])

    # measure height
    dummy = Image.new('RGB', (width, 10), (18, 18, 18))
    draw = ImageDraw.Draw(dummy)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    height = pad * 2 + len(lines) * (line_h + line_gap)

    img = Image.new('RGB', (width, height), (18, 18, 18))
    draw = ImageDraw.Draw(img)

    y = pad
    for i, line in enumerate(lines):
        color = (235, 235, 235)
        if i == 0:
            color = (255, 210, 80)
        draw.text((pad, y), line, font=font, fill=color)
        y += line_h + line_gap

    # add a subtle border
    draw.rectangle((0, 0, width - 1, height - 1), outline=(50, 50, 50))

    import io

    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def get_or_render_png(
    *,
    text: str,
    title: str,
    out_dir: Path,
    cfg: ImageRenderConfig,
    key_prefix: str = 'wf',
) -> Path | None:
    if not cfg.enabled:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    key = _hash_text(title + '\n' + text)
    name = f"{_safe_filename(key_prefix)}_{key}.png"
    path = out_dir / name

    if cfg.cache_images and path.exists():
        return path

    data = render_text_to_png_bytes(text, title=title)
    path.write_bytes(data)

    if cfg.cache_images and cfg.keep_images > 0:
        _trim_png_cache(out_dir, keep=cfg.keep_images)

    return path


def _trim_png_cache(out_dir: Path, *, keep: int) -> None:
    if keep <= 0:
        return
    files = sorted([p for p in out_dir.glob('*.png') if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[keep:]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
