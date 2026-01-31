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


def to_simplified_zh(text: str) -> str:
    if not text:
        return text
    cc = _get_opencc()
    if cc is None:
        zc = _get_zhconv()
        if zc is None:
            return text
        try:
            return zc.convert(text, "zh-cn")
        except Exception:
            return text
    try:
        return cc.convert(text)
    except Exception:
        return text

