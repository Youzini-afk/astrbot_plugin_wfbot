from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib import request, error


def _normalize_base(raw: str) -> str:
    base = raw.strip()
    if not base:
        return ""
    if not (base.startswith("http://") or base.startswith("https://")):
        base = "https://" + base
    return base.rstrip("/")


def _build_candidates(base: str, lang: str) -> list[str]:
    if not base:
        return []
    root = base
    if not root.endswith("/pc"):
        root = root + "/pc"
    candidates: list[str] = []
    cache_buster = str(int(time.time()))
    variants = [
        f"{root}",
        f"{root}/",
        f"{root}?language={lang}",
        f"{root}/?language={lang}",
        f"{root}?language={lang}&_ts={cache_buster}",
        f"{root}/?language={lang}&_ts={cache_buster}",
    ]
    candidates.extend(variants)
    return candidates


def _fetch_json(url: str, timeout: float) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or 0
            if status < 200 or status >= 400:
                return None
            data = resp.read()
    except error.HTTPError:
        return None
    except Exception:
        return None
    if not data:
        return None
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if "error" in payload:
        return None
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch warframestat root worldstate and save locally.")
    parser.add_argument("--lang", default="zh", help="Language code (default: zh)")
    parser.add_argument("--out", default="worldstate/warframestat_root.json", help="Output JSON path")
    parser.add_argument("--base", default="https://api.warframestat.us", help="Base URL (without /pc)")
    parser.add_argument("--mirror", action="append", default=[], help="Mirror base URL (repeatable)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout seconds")
    args = parser.parse_args()

    bases = [_normalize_base(args.base)]
    for m in args.mirror:
        nm = _normalize_base(m)
        if nm:
            bases.append(nm)

    candidates: list[str] = []
    for base in bases:
        candidates.extend(_build_candidates(base, args.lang))

    for url in candidates:
        payload = _fetch_json(url, timeout=args.timeout)
        if not payload:
            continue
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"OK: saved to {out_path} from {url}")
        return 0

    print("ERROR: all candidates failed; no data written.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
