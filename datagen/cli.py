"""Command line entry point.

  python -m datagen run       clean target, then generate + upload all batches
  python -m datagen clean     only delete previously loaded data
  python -m datagen plan      validate config + datasets and show what would be loaded
"""
from __future__ import annotations

import argparse
import sys
import time

from .config import ConfigError, load_config
from .generator import Generator, generation_order, load_specs
from .sinks import AdlsSink, LocalSink, to_bytes


def _sink(cfg, local):
    return LocalSink(local, cfg) if local else AdlsSink(cfg)


def _rel_path(cfg, spec, batch, fmt):
    try:
        return cfg.path_template.format(
            dataset=spec.name, batch_id=batch.id, batch_start=batch.start,
            batch_end=batch.end, ext=fmt,
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"output.path_template is invalid: {e}") from e


def cmd_plan(args):
    cfg = load_config(args.config)
    specs = load_specs(cfg.datasets_dir)
    order = generation_order(specs)
    target = args.local or f"abfss://{cfg.container}@{cfg.account_name}.dfs.core.windows.net/{cfg.root_path}"
    print(f"Target       : {target}")
    print(f"Datasets     : {', '.join(order)}  (from {cfg.datasets_dir})")
    print(f"Batches      : {len(cfg.batches)}")
    for b in cfg.batches[:5]:
        print(f"  #{b.id:<4} {b.start}  ->  {b.end}")
    if len(cfg.batches) > 5:
        print(f"  ... last #{cfg.batches[-1].id} {cfg.batches[-1].start} -> {cfg.batches[-1].end}")
    b = cfg.batches[0]
    print("Example paths:")
    for name in order:
        fmt = (specs[name].format or cfg.default_format).lower()
        print(f"  {_rel_path(cfg, specs[name], b, fmt)}")
    if not args.local:
        cfg.access_key()  # fail early if the env var is missing
        print(f"Access key   : found in env var {cfg.access_key_env}")


def cmd_clean(args):
    cfg = load_config(args.config)
    _sink(cfg, args.local).clean()


def cmd_run(args):
    t0 = time.time()
    cfg = load_config(args.config)
    specs = load_specs(cfg.datasets_dir)
    order = generation_order(specs)
    sink = _sink(cfg, args.local)

    print(f"Target  : {sink.label}")
    print(f"Datasets: {', '.join(order)}   Batches: {len(cfg.batches)}")

    if args.no_clean:
        print("Skipping clean (--no-clean)")
    else:
        print("Cleaning previously loaded data ...")
        sink.clean()

    gen = Generator(specs, cfg.seed, cfg.locale)
    totals = {n: 0 for n in order}
    files = 0
    for batch in cfg.batches:
        gen.start_batch()
        for name in order:
            spec = specs[name]
            fmt = (spec.format or cfg.default_format).lower()
            data = gen.generate(spec, batch)
            rows = len(next(iter(data.values()), []))
            path = sink.write(_rel_path(cfg, spec, batch, fmt), to_bytes(data, spec, fmt))
            totals[name] += rows
            files += 1
            print(f"  batch {batch.id:>4}/{len(cfg.batches)}  {name:<20} {rows:>8} rows -> {path}")

    print(f"\nDone in {time.time() - t0:.1f}s: {files} file(s)")
    for n, c in totals.items():
        print(f"  {n:<20} {c:>10} rows")


def main(argv=None):
    p = argparse.ArgumentParser(prog="datagen", description="Synthetic data generator for ADLS Gen2")
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_ in (
        ("run", cmd_run, "clean target, then generate and load all batches"),
        ("clean", cmd_clean, "delete previously loaded data only"),
        ("plan", cmd_plan, "validate config/datasets and show the batch plan"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("-c", "--config", default="config.yaml", help="path to config yaml (default: config.yaml)")
        sp.add_argument("--local", metavar="DIR", help="write to a local folder instead of ADLS (for testing)")
        if name == "run":
            sp.add_argument("--no-clean", action="store_true", help="append without deleting existing data")
        sp.set_defaults(func=fn)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
