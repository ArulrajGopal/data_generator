"""Dataset JSON specs and the row generator."""
from __future__ import annotations

import json
import random
import string
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker

from .config import Batch, ConfigError, FORMATS, parse_dt

# type -> required keys
COLUMN_TYPES: dict[str, tuple[str, ...]] = {
    "sequence": (),
    "uuid": (),
    "int": ("min", "max"),
    "float": (),
    "string": (),
    "choice": ("values",),
    "bool": (),
    "date": (),
    "timestamp": (),
    "faker": ("provider",),
    "template": ("pattern",),
    "reference": ("dataset", "column"),
    "batch": ("field",),
    "constant": ("value",),
}
INT_TYPES = {"sequence", "int"}


class DatasetSpec:
    def __init__(self, raw: dict, source: Path):
        self.source = source
        self.name: str = raw.get("name") or source.stem
        self.rows = raw.get("rows_per_batch", 100)
        self.format: str | None = raw.get("format")
        self.columns: list[dict] = raw.get("columns") or []
        self._validate()

    def _err(self, msg: str):
        raise ConfigError(f"{self.source.name}: {msg}")

    def _validate(self):
        if self.format and self.format.lower() not in FORMATS:
            self._err(f"format must be one of {FORMATS}")
        if isinstance(self.rows, dict):
            if not {"min", "max"} <= self.rows.keys() or self.rows["min"] > self.rows["max"]:
                self._err("rows_per_batch as object needs min <= max")
        elif not isinstance(self.rows, int) or self.rows < 0:
            self._err("rows_per_batch must be a non-negative int or {min, max}")
        if not self.columns:
            self._err("no columns defined")
        seen = set()
        for c in self.columns:
            name, t = c.get("name"), c.get("type")
            if not name:
                self._err(f"column without name: {c}")
            if name in seen:
                self._err(f"duplicate column {name!r}")
            seen.add(name)
            if t not in COLUMN_TYPES:
                self._err(f"column {name!r}: unknown type {t!r}. Use one of {list(COLUMN_TYPES)}")
            missing = [k for k in COLUMN_TYPES[t] if k not in c]
            if missing:
                self._err(f"column {name!r} ({t}) missing {missing}")
            if t == "batch" and c["field"] not in ("id", "start", "end"):
                self._err(f"column {name!r}: batch.field must be id|start|end")
            if t == "choice" and c.get("weights") and len(c["weights"]) != len(c["values"]):
                self._err(f"column {name!r}: weights and values differ in length")

    @property
    def dependencies(self) -> set[str]:
        return {c["dataset"] for c in self.columns if c["type"] == "reference"} - {self.name}

    def row_count(self, rng: random.Random) -> int:
        if isinstance(self.rows, dict):
            return rng.randint(self.rows["min"], self.rows["max"])
        return self.rows


def load_specs(folder: Path) -> dict[str, DatasetSpec]:
    if not folder.is_dir():
        raise ConfigError(f"Datasets folder not found: {folder}")
    specs: dict[str, DatasetSpec] = {}
    for p in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{p.name}: invalid JSON - {e}") from e
        spec = DatasetSpec(raw, p)
        if spec.name in specs:
            raise ConfigError(f"Dataset name {spec.name!r} defined twice")
        specs[spec.name] = spec
    if not specs:
        raise ConfigError(f"No *.json dataset files in {folder}")
    for s in specs.values():
        for c in s.columns:
            if c["type"] == "reference":
                tgt = specs.get(c["dataset"])
                if not tgt:
                    raise ConfigError(f"{s.source.name}: column {c['name']!r} references unknown dataset {c['dataset']!r}")
                if c["column"] not in {x["name"] for x in tgt.columns}:
                    raise ConfigError(f"{s.source.name}: column {c['name']!r} references unknown column {c['dataset']}.{c['column']}")
    return specs


def generation_order(specs: dict[str, DatasetSpec]) -> list[str]:
    """Topological order so referenced (parent) datasets are generated first."""
    order, done, visiting = [], set(), set()

    def visit(n):
        if n in done:
            return
        if n in visiting:
            raise ConfigError(f"Circular reference between datasets involving {n!r}")
        visiting.add(n)
        for d in sorted(specs[n].dependencies):
            visit(d)
        visiting.discard(n)
        done.add(n)
        order.append(n)

    for n in sorted(specs):
        visit(n)
    return order


class Generator:
    def __init__(self, specs: dict[str, DatasetSpec], seed: int | None, locale: str):
        self.rng = random.Random(seed)
        self.faker = Faker(locale)
        if seed is not None:
            self.faker.seed_instance(seed)
        self.sequences: dict[tuple[str, str], int] = {}
        # only keep values of columns that something references
        self.referenced = {
            (c["dataset"], c["column"])
            for s in specs.values() for c in s.columns if c["type"] == "reference"
        }
        self.pool_all: dict[tuple[str, str], list] = {}
        self.pool_batch: dict[tuple[str, str], list] = {}

    def start_batch(self):
        self.pool_batch = {}

    # ------------------------------------------------------------------ public
    def generate(self, spec: DatasetSpec, batch: Batch) -> dict[str, list]:
        n = spec.row_count(self.rng)
        data: dict[str, list] = {}
        for col in spec.columns:
            try:
                data[col["name"]] = self._column(col, n, data, batch, spec.name)
            except ConfigError:
                raise
            except Exception as e:  # surface which column broke
                raise ConfigError(f"{spec.source.name}: column {col['name']!r}: {e}") from e

        for col in spec.columns:  # nulls applied last so templates/refs see real values
            ratio = float(col.get("null_ratio", 0) or 0)
            if ratio:
                data[col["name"]] = [None if self.rng.random() < ratio else v for v in data[col["name"]]]

        for name, vals in data.items():
            key = (spec.name, name)
            if key in self.referenced:
                clean = [v for v in vals if v is not None]
                self.pool_batch.setdefault(key, []).extend(clean)
                self.pool_all.setdefault(key, []).extend(clean)
        return data

    # ------------------------------------------------------------------ columns
    def _window(self, col: dict, batch: Batch) -> tuple[datetime, datetime]:
        lo = parse_dt(col["min"]) if "min" in col else batch.start
        hi = parse_dt(col["max"], inclusive_end=True) if "max" in col else batch.end
        if hi <= lo:
            raise ConfigError(f"column {col['name']!r}: max must be after min")
        return lo, hi

    def _column(self, col: dict, n: int, data: dict, batch: Batch, ds: str) -> list:
        r, t = self.rng, col["type"]

        if t == "sequence":
            key, step = (ds, col["name"]), int(col.get("step", 1))
            cur = self.sequences.get(key, int(col.get("start", 1)))
            self.sequences[key] = cur + n * step  # continues across batches
            return [cur + i * step for i in range(n)]

        if t == "uuid":
            return [str(uuid.UUID(int=r.getrandbits(128), version=4)) for _ in range(n)]

        if t == "int":
            return [r.randint(int(col["min"]), int(col["max"])) for _ in range(n)]

        if t == "float":
            lo, hi = float(col.get("min", 0)), float(col.get("max", 1))
            p = int(col.get("precision", 2))
            if col.get("distribution") == "normal":
                mean = float(col.get("mean", (lo + hi) / 2))
                std = float(col.get("std", (hi - lo) / 6 or 1))
                return [round(min(max(r.gauss(mean, std), lo), hi), p) for _ in range(n)]
            return [round(r.uniform(lo, hi), p) for _ in range(n)]

        if t == "string":
            chars = col.get("chars", string.ascii_letters + string.digits)
            lo = int(col.get("min_length", col.get("length", 8)))
            hi = int(col.get("max_length", col.get("length", lo)))
            pre = col.get("prefix", "")
            return [pre + "".join(r.choices(chars, k=r.randint(lo, hi))) for _ in range(n)]

        if t == "choice":
            return r.choices(col["values"], weights=col.get("weights"), k=n)

        if t == "bool":
            ratio = float(col.get("true_ratio", 0.5))
            return [r.random() < ratio for _ in range(n)]

        if t in ("date", "timestamp"):
            lo, hi = self._window(col, batch)
            span = (hi - lo).total_seconds()
            vals = [lo + timedelta(seconds=int(r.random() * span)) for _ in range(n)]
            if t == "date":
                vals = [v.date() for v in vals]
            if col.get("sorted"):
                vals.sort()
            fmt = col.get("format")
            return [v.strftime(fmt) for v in vals] if fmt else vals

        if t == "faker":
            src = self.faker.unique if col.get("unique") else self.faker
            fn = getattr(src, col["provider"], None)
            if fn is None:
                raise ConfigError(f"column {col['name']!r}: unknown faker provider {col['provider']!r}")
            args = col.get("args", {})
            return [fn(**args) for _ in range(n)]

        if t == "template":
            names = list(data)
            out = []
            for i in range(n):
                ctx = {k: data[k][i] for k in names}
                ctx.update(batch_id=batch.id, batch_start=batch.start, batch_end=batch.end, row_number=i + 1)
                out.append(col["pattern"].format(**ctx))
            return out

        if t == "reference":
            key = (col["dataset"], col["column"])
            pool = (self.pool_all if col.get("scope") == "all" else self.pool_batch).get(key) or []
            if not pool and n:
                raise ConfigError(
                    f"column {col['name']!r}: no values yet in {key[0]}.{key[1]} "
                    "(does the referenced dataset generate rows in this batch?)"
                )
            return [r.choice(pool) for _ in range(n)]

        if t == "batch":
            v = {"id": batch.id, "start": batch.start, "end": batch.end}[col["field"]]
            return [v] * n

        if t == "constant":
            return [col["value"]] * n

        raise ConfigError(f"unknown type {t}")
