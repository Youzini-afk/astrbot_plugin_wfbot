from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import aiofiles


@dataclass(frozen=True)
class ImageRenderConfig:
    enabled: bool = False
    cache_images: bool = False
    keep_images: int = 10
    width: int = 1080
    font_size: int = 32
    title_font_size: int = 40
    pad: int = 28
    line_gap: int = 10
    font_cjk: str | None = None
    font_emoji: str | None = None
    emoji_mode: str = "replace"  # keep|replace|strip


def _safe_filename(stem: str) -> str:
    return ''.join(c for c in stem if c.isalnum() or c in ('-', '_'))[:64] or 'wf'


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()


def _is_emoji_cluster(s: str) -> bool:
    if not s:
        return False
    # Rough heuristic: cover most emoji blocks + common symbols with VS16.
    for ch in s:
        o = ord(ch)
        if ch in {"\u200d", "\ufe0f"}:
            continue
        # Misc Technical (includes ⏳ U+23F3) / Dingbats / Misc symbols.
        if 0x2300 <= o <= 0x23FF:
            return True
        if 0x1F000 <= o <= 0x1FAFF:
            return True
        if 0x2600 <= o <= 0x26FF:
            return True
        if 0x2700 <= o <= 0x27BF:
            return True
    return False


def _split_clusters(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    cur = ""
    for ch in text:
        if ch in {"\u200d", "\ufe0f"} and out:
            out[-1] = out[-1] + ch
            continue
        out.append(ch)
    # merge surrogate-like sequences already handled by Python; keep simple.
    return out


@lru_cache(maxsize=1)
def _default_font_dirs() -> list[Path]:
    dirs: list[Path] = []
    win = os.environ.get("WINDIR")
    if win:
        dirs.append(Path(win) / "Fonts")
    dirs.extend(
        [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
        ]
    )
    return [d for d in dirs if d.exists()]


def _find_font_path(candidates: list[str]) -> str | None:
    for folder in _default_font_dirs():
        for name in candidates:
            p = folder / name
            if p.exists():
                return str(p)
    return None


def _load_font(path: str | None, size: int):
    from PIL import ImageFont

    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return None


def _pick_fonts(cfg: ImageRenderConfig):
    from PIL import ImageFont

    cjk = _load_font(cfg.font_cjk, cfg.font_size)
    emoji = _load_font(cfg.font_emoji, cfg.font_size)
    title = _load_font(cfg.font_cjk, cfg.title_font_size)

    if cjk is None:
        cjk = _load_font(
            _find_font_path(
                [
                    "msyh.ttc",  # Microsoft YaHei
                    "msyhbd.ttc",
                    "simhei.ttf",
                    "simsun.ttc",
                    "PingFang.ttc",
                    "NotoSansCJK-Regular.ttc",
                    "NotoSansCJKsc-Regular.otf",
                    "SourceHanSansSC-Regular.otf",
                    "DejaVuSans.ttf",
                ]
            ),
            cfg.font_size,
        )
    if title is None:
        title = _load_font(cfg.font_cjk, cfg.title_font_size)
        if title is None:
            title = _load_font(_find_font_path(["msyh.ttc", "simhei.ttf", "DejaVuSans.ttf"]), cfg.title_font_size)
    if emoji is None:
        emoji = _load_font(
            _find_font_path(
                [
                    "seguisym.ttf",  # Segoe UI Symbol (often better supported than color emoji)
                    "SegoeUISymbol.ttf",
                    "seguiemj.ttf",  # Segoe UI Emoji
                    "SegoeUIEmoji.ttf",
                    "Apple Color Emoji.ttc",
                    "NotoColorEmoji.ttf",
                    "Symbola.ttf",
                ]
            ),
            cfg.font_size,
        )

    # Final fallback: Pillow default font (ASCII only).
    if cjk is None:
        cjk = ImageFont.load_default()
    if title is None:
        title = cjk
    return title, cjk, emoji


def _text_width(draw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except Exception:
        try:
            box = draw.textbbox((0, 0), text, font=font)
            return int(box[2] - box[0])
        except Exception:
            return len(text) * 10


def _wrap_line(draw, line: str, font, max_width: int) -> list[str]:
    if not line:
        return [""]
    if _text_width(draw, line, font) <= max_width:
        return [line]
    # Prefer breaking on spaces; otherwise break by characters (works for CJK).
    if " " in line:
        words = line.split(" ")
        out: list[str] = []
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip() if cur else w
            if _text_width(draw, cand, font) <= max_width:
                cur = cand
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
        return out or [line]

    out: list[str] = []
    cur = ""
    for ch in line:
        cand = cur + ch
        if _text_width(draw, cand, font) <= max_width:
            cur = cand
            continue
        if cur:
            out.append(cur)
            cur = ch
        else:
            out.append(ch)
            cur = ""
    if cur:
        out.append(cur)
    return out or [line]


def _draw_mixed_text(draw, xy: tuple[int, int], line: str, *, font_cjk, font_emoji, fill) -> None:
    x, y = xy
    clusters = _split_clusters(line)
    for cl in clusters:
        # Prefer emoji/symbol font for emoji-like clusters.
        font = font_emoji if (font_emoji is not None and _is_emoji_cluster(cl)) else font_cjk
        # If emoji font doesn't have this glyph, fall back to CJK font to avoid tofu boxes.
        try:
            if font is font_emoji:
                bbox = draw.textbbox((0, 0), cl, font=font_emoji)
                if (bbox[2] - bbox[0]) <= 0:
                    font = font_cjk
        except Exception:
            pass
        draw.text((x, y), cl, font=font, fill=fill)
        x += _text_width(draw, cl, font)


def _is_emoji_char(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    if 0x1F000 <= o <= 0x1FAFF:
        return True
    if 0x2300 <= o <= 0x23FF:
        return True
    if 0x2600 <= o <= 0x26FF:
        return True
    if 0x2700 <= o <= 0x27BF:
        return True
    return False


def _strip_emojis(s: str) -> str:
    out = []
    for ch in s:
        if ch in {"\u200d", "\ufe0f"}:
            continue
        if _is_emoji_char(ch):
            continue
        out.append(ch)
    return "".join(out)


_EMOJI_REPLACEMENTS: dict[str, str] = {
    "🚨": "!",
    "🌀": "裂",
    "🔄": "刷",
    "⏳": "剩余",
    "⏱️": "剩余",
    "⚔️": "⚔",
    "🎯": "→",
    "🛡️": "盾",
    "🆘": "SOS",
    "🕵️": "侦",
    "💥": "爆",
    "⛏️": "挖",
    "📡": "讯",
    "🧪": "试",
    "🚩": "旗",
    "🧠": "智",
    "☣️": "毒",
    "🌍": "地",
    "🌾": "原",
    "❄️": "冷",
    "🔥": "热",
    "🦠": "疫",
    "🚢": "船",
    "☀️": "日",
    "🌙": "夜",
    "🟥": "F",
    "🟦": "V",
}


def _prepare_text_for_rendering(text: str, *, cfg: ImageRenderConfig) -> str:
    mode = (cfg.emoji_mode or "replace").strip().lower()
    if mode == "keep":
        return text
    if mode == "strip":
        return _strip_emojis(text)
    # replace
    s = text
    for k, v in _EMOJI_REPLACEMENTS.items():
        s = s.replace(k, v)
    # remove any remaining emoji-like chars to avoid tofu/garbling
    return _strip_emojis(s)


def render_text_to_png_bytes(text: str, *, title: str = 'Warframe', cfg: ImageRenderConfig | None = None) -> bytes:
    # Pillow-based, no browser rendering.
    from PIL import Image, ImageDraw

    cfg = cfg or ImageRenderConfig()
    width = int(max(480, cfg.width))
    pad = int(max(8, cfg.pad))
    line_gap = int(max(0, cfg.line_gap))

    title_font, body_font, emoji_font = _pick_fonts(cfg)

    title2 = _prepare_text_for_rendering(title, cfg=cfg)
    text2 = _prepare_text_for_rendering(text, cfg=cfg)
    raw_lines = text2.splitlines() if text2 else ["(empty)"]
    lines = [title2, ""] + raw_lines

    dummy = Image.new("RGB", (width, 10), (18, 18, 18))
    draw = ImageDraw.Draw(dummy)
    max_line_w = width - pad * 2

    wrapped: list[tuple[str, object, tuple[int, int, int]]] = []
    for i, line in enumerate(lines):
        if i == 0:
            font = title_font
            color = (255, 210, 80)
        else:
            font = body_font
            color = (235, 235, 235)
        for seg in _wrap_line(draw, line, font, max_line_w):
            wrapped.append((seg, font, color))

    def line_height(font) -> int:
        try:
            box = draw.textbbox((0, 0), "Ag", font=font)
            return int(box[3] - box[1])
        except Exception:
            return int(cfg.font_size * 1.25)

    height = pad * 2 + sum(line_height(font) + line_gap for _, font, _ in wrapped) + 2
    img = Image.new("RGB", (width, int(max(120, height))), (18, 18, 18))
    draw = ImageDraw.Draw(img)

    y = pad
    for line, font, color in wrapped:
        if font is body_font:
            _draw_mixed_text(draw, (pad, y), line, font_cjk=body_font, font_emoji=emoji_font, fill=color)
        else:
            draw.text((pad, y), line, font=font, fill=color)
        y += line_height(font) + line_gap

    draw.rectangle((0, 0, width - 1, img.size[1] - 1), outline=(50, 50, 50))

    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def get_or_render_png(
    *,
    text: str,
    title: str,
    out_dir: Path,
    cfg: ImageRenderConfig,
    key_prefix: str = 'wf',
) -> Path | None:
    if not cfg.enabled:
        return None

    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    # Include render parameters to avoid stale cache after config changes (e.g. font size).
    key = _hash_text(
        "\n".join(
            [
                title,
                text,
                f"w={cfg.width}",
                f"fs={cfg.font_size}",
                f"ts={cfg.title_font_size}",
                f"pad={cfg.pad}",
                f"gap={cfg.line_gap}",
                f"cjk={cfg.font_cjk or ''}",
                f"emo={cfg.font_emoji or ''}",
                f"emode={cfg.emoji_mode}",
            ]
        )
    )
    name = f"{_safe_filename(key_prefix)}_{key}.png"
    path = out_dir / name

    if cfg.cache_images:
        try:
            if await asyncio.to_thread(path.exists):
                return path
        except Exception:
            pass

    data = await asyncio.to_thread(render_text_to_png_bytes, text, title=title, cfg=cfg)
    async with aiofiles.open(path, "wb") as f:
        await f.write(data)

    if cfg.cache_images and cfg.keep_images > 0:
        await asyncio.to_thread(_trim_png_cache, out_dir, keep=cfg.keep_images)

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
