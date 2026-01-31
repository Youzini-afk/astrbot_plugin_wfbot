#!/usr/bin/env python3
"""Test script to examine worldstate structure and syndicate cycles."""

import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    data_path = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_wfbot"
except Exception as e:
    print(f"Failed to get astrbot data path: {e}")
    import sys
    data_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

# Try to find and load cached worldstate
cache_file = data_path / "mirrors" / "latest.json"
if not cache_file.exists():
    cache_file = data_path / "worldstate" / "latest.parsed.json"
if not cache_file.exists():
    cache_file = data_path / "cache" / "worldstate" / "latest.parsed.json"

print(f"Looking for cache file...")
print(f"  Option 1: {data_path / 'mirrors' / 'latest.json'}")
print(f"  Option 2: {data_path / 'worldstate' / 'latest.parsed.json'}")
print(f"  Option 3: {data_path / 'cache' / 'worldstate' / 'latest.parsed.json'}")

# Search recursively
for possible_path in data_path.rglob("latest.parsed.json"):
    print(f"\nFound: {possible_path}")
    try:
        with open(possible_path) as f:
            ws = json.load(f)
        
        print(f"Worldstate keys: {list(ws.keys())}")
        
        # Check for cycle fields
        for cycle_key in ["cetusCycle", "vallisCycle", "cambionCycle", "zarimanCycle"]:
            cycle_data = ws.get(cycle_key)
            if cycle_data:
                print(f"\n{cycle_key}: {cycle_data}")
        
        # Check SyndicateMissions
        syndicate_missions = ws.get("SyndicateMissions")
        if syndicate_missions:
            print(f"\nFound {len(syndicate_missions)} SyndicateMissions:")
            now_ts = float(ws.get("Time") or datetime.now(timezone.utc).timestamp())
            for mission in syndicate_missions[:10]:
                tag = mission.get("Tag", "?")
                activation = mission.get("Activation")
                expiry = mission.get("Expiry")
                print(f"  {tag}: activation={activation}, expiry={expiry}")
                
                # Try to parse as timestamp
                if isinstance(expiry, (int, float)):
                    if expiry > 1e12:
                        expiry_ts = expiry / 1000.0
                    else:
                        expiry_ts = expiry
                    remaining = expiry_ts - now_ts
                    print(f"    -> expiry_ts={expiry_ts}, remaining={remaining}s ({remaining/60:.1f}min)")
        
        break
    except Exception as e:
        print(f"Error reading {possible_path}: {e}")
        import traceback
        traceback.print_exc()

# Try to find worldstate in different locations
print("\n\nSearching for all cache files...")
for cache_path in data_path.rglob("*.json"):
    rel_path = cache_path.relative_to(data_path)
    if "worldstate" in str(rel_path) or "SyndicateMissions" in cache_path.read_text(errors="ignore"):
        print(f"  {rel_path}")
