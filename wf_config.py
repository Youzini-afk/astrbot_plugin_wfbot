from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WarframeConfig:
    # Data directory; in AstrBot plugins this should be resolved via StarTools.get_data_dir(<plugin_name>).
    # This default is only for non-AstrBot ad-hoc usage; WarframeDataSource.create() will attempt to
    # resolve a better default automatically.
    data_dir: Path = Path(".")

    # Timeouts (seconds)
    connect_timeout: float = 5.0
    read_timeout: float = 15.0

    # Retry policy
    retries: int = 2
    retry_backoff_seconds: float = 2.0

    # Data refresh intervals (seconds). Set to 0 to disable background refresh for that job.
    worldstate_refresh_interval: float = 600.0  # NyxBot: every 10 min
    public_export_refresh_interval: float = 6 * 3600.0
    mirrors_refresh_interval: float = 6 * 3600.0
    market_bootstrap_refresh_interval: float = 24 * 3600.0  # items/weapons lists etc.

    # Data management
    worldstate_cache_ttl_seconds: float = 180.0  # NyxBot: 3 min cache
    keep_worldstate_snapshots: int = 24
    keep_mirror_snapshots: int = 10
    cleanup_retention_days: int = 14

    # Defaults
    public_export_language: str = "zh"

    # Persistence backends
    # Note: some environments/filesystems can't support sqlite file locking; keep it optional.
    enable_sqlite: bool = False
    sqlite_path: Path = Path("storage.sqlite3")
    enable_file_store: bool = True

