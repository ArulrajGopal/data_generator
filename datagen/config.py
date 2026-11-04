"""Loading config.yaml, resolving the access key and building the batch plan."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------------- dates
def parse_dt(value, *, inclusive_end: bool = False) -> datetime:
    """Accept YAML date/datetime objects or ISO strings.

    A date-only value used as an end bound is treated as inclusive, i.e. the
    whole day is included (2026-01-07 -> 2026-01-08 00:00 exclusive).
    """
    if isinstance(value, datetime):
        return value
    date_only = False
    if isinstance(value, date):
        dt, date_only = datetime(value.year, value.month, value.day), True
    elif isinstance(value, str):
        s = value.strip()
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as e:
            raise ConfigError(f"Invalid date/datetime: {value!r}") from e
        date_only = len(s) == 10
    else:
        raise ConfigError(f"Invalid date/datetime: {value!r}")
    if inclusive_end and date_only:
        dt += timedelta(days=1)
    return dt


_UNITS = {
    "s": lambda n: relativedelta(seconds=n),
    "min": lambda n: relativedelta(minutes=n),
    "m": lambda n: relativedelta(minutes=n),
    "h": lambda n: relativedelta(hours=n),
    "d": lambda n: relativedelta(days=n),
    "w": lambda n: relativedelta(weeks=n),
    "mo": lambda n: relativedelta(months=n),
    "y": lambda n: relativedelta(years=n),
}


def parse_interval(value: str) -> relativedelta:
    """'30min', '6h', '1d', '1w', '1mo', '1y' (also '15m' = minutes)."""
    m = re.fullmatch(r"\s*(\d+)\s*(s|min|m|h|d|w|mo|y)\s*", str(value))
    if not m:
        raise ConfigError(
            f"Invalid interval {value!r}. Use e.g. 30min, 6h, 1d, 1w, 1mo, 1y"
        )
    n = int(m.group(1))
    if n <= 0:
        raise ConfigError("interval must be > 0")
    return _UNITS[m.group(2)](n)


# --------------------------------------------------------------------------- batches
@dataclass
class Batch:
    id: int
    start: datetime
    end: datetime  # exclusive


def build_batches(b: dict) -> list[Batch]:
    """Supported combinations (see config.yaml):
    start_date + end_date + interval -> one batch per interval in the range
    start_date + interval + count     -> `count` consecutive batches
    start_date + end_date + count     -> range split into `count` equal batches
    count only                        -> `count` daily batches starting today
    """
    count = b.get("count")
    if count is not None and (not isinstance(count, int) or count <= 0):
        raise ConfigError("batches.count must be a positive integer")

    start = (
        parse_dt(b["start_date"])
        if b.get("start_date")
        else datetime.combine(date.today(), datetime.min.time())
    )
    end = parse_dt(b["end_date"], inclusive_end=True) if b.get("end_date") else None
    interval = parse_interval(b["interval"]) if b.get("interval") else None

    if end is not None and end <= start:
        raise ConfigError("batches.end_date must be after start_date")

    windows: list[tuple[datetime, datetime]] = []
    if end is not None and count and not interval:
        step = (end - start) / count
        windows = [(start + i * step, start + (i + 1) * step) for i in range(count)]
    else:
        if interval is None:
            if not count:
                raise ConfigError(
                    "batches: give at least `count`, or `end_date` + `interval`"
                )
            interval = relativedelta(days=1)
        if end is None and not count:
            raise ConfigError("batches: with `interval` you need `end_date` or `count`")
        cur = start
        while (end is None or cur < end) and (not count or len(windows) < count):
            nxt = cur + interval
            windows.append((cur, min(nxt, end) if end else nxt))
            cur = nxt
            if len(windows) > 100_000:
                raise ConfigError("batches: more than 100000 batches - check interval")

    return [Batch(i + 1, s, e) for i, (s, e) in enumerate(windows)]


# --------------------------------------------------------------------------- secrets
def get_env_var(name: str) -> str | None:
    """Read an env var; on Windows fall back to the registry so a variable set
    with `setx` (or System Properties) works without restarting the terminal."""
    val = os.environ.get(name)
    if val:
        return val
    if sys.platform == "win32":
        import winreg

        locations = [
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ]
        for hive, path in locations:
            try:
                with winreg.OpenKey(hive, path) as key:
                    val, _ = winreg.QueryValueEx(key, name)
                    if val:
                        return os.path.expandvars(str(val))
            except OSError:
                continue
    return None


# --------------------------------------------------------------------------- config
@dataclass
class Config:
    path: Path
    account_name: str
    container: str
    access_key_env: str
    root_path: str
    allow_clean_container_root: bool
    datasets_dir: Path
    default_format: str
    path_template: str
    batches: list[Batch]
    seed: int | None
    locale: str

    def access_key(self) -> str:
        key = get_env_var(self.access_key_env)
        if not key:
            raise ConfigError(
                f"Environment variable {self.access_key_env!r} is not set. Set it with:\n"
                f'  setx {self.access_key_env} "<storage-account-access-key>"'
            )
        return key


FORMATS = ("csv", "parquet", "json")


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    adls = raw.get("adls") or {}
    for k in ("account_name", "container", "access_key_env"):
        if not adls.get(k):
            raise ConfigError(f"config: adls.{k} is required")

    out = raw.get("output") or {}
    fmt = str(out.get("format", "csv")).lower()
    if fmt not in FORMATS:
        raise ConfigError(f"output.format must be one of {FORMATS}")

    gen = raw.get("generation") or {}
    ds_dir = Path(raw.get("datasets_dir", "datasets"))
    if not ds_dir.is_absolute():
        ds_dir = (path.parent / ds_dir).resolve()

    return Config(
        path=path,
        account_name=str(adls["account_name"]),
        container=str(adls["container"]),
        access_key_env=str(adls["access_key_env"]),
        root_path=str(adls.get("root_path", "") or "").strip("/"),
        allow_clean_container_root=bool(adls.get("allow_clean_container_root", False)),
        datasets_dir=ds_dir,
        default_format=fmt,
        path_template=str(
            out.get(
                "path_template",
                "{dataset}/load_date={batch_start:%Y-%m-%d}/{dataset}_{batch_id:04d}.{ext}",
            )
        ),
        batches=build_batches(raw.get("batches") or {}),
        seed=gen.get("seed"),
        locale=str(gen.get("locale", "en_US")),
    )
