from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _get_opencc():
    try:
        from opencc import OpenCC  # type: ignore

        return OpenCC("t2s")
    except Exception:
        return None


@lru_cache(maxsize=1)
def _get_zhconv():
    try:
        import zhconv  # type: ignore

        return zhconv
    except Exception:
        return None


_TRADITIONAL_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("歐羅巴", "欧罗巴"),
    ("賽德娜", "赛德娜"),
    ("鬩神星", "阋神星"),
    ("穀神星", "谷神星"),
)


def _apply_zh_overrides(text: str) -> str:
    if not text:
        return text
    out = text
    for src, dst in _TRADITIONAL_OVERRIDES:
        if src in out:
            out = out.replace(src, dst)
    return out


def to_simplified_zh(text: str) -> str:
    if not text:
        return text
    cc = _get_opencc()
    if cc is None:
        zc = _get_zhconv()
        if zc is None:
            return _apply_zh_overrides(text)
        try:
            return _apply_zh_overrides(zc.convert(text, "zh-cn"))
        except Exception:
            return _apply_zh_overrides(text)
    try:
        return _apply_zh_overrides(cc.convert(text))
    except Exception:
        return _apply_zh_overrides(text)

