"""Serialisation and storage targets (ADLS Gen2 or a local folder)."""
from __future__ import annotations

import io
import shutil
from pathlib import Path

import pandas as pd

from .config import Config, ConfigError
from .generator import INT_TYPES, DatasetSpec


def to_bytes(data: dict[str, list], spec: DatasetSpec, fmt: str) -> bytes:
    df = pd.DataFrame(data, columns=[c["name"] for c in spec.columns])
    for c in spec.columns:  # keep ints/bools as ints/bools even when nulls are present
        if c["type"] in INT_TYPES or (c["type"] == "batch" and c["field"] == "id"):
            df[c["name"]] = df[c["name"]].astype("Int64")
        elif c["type"] == "bool":
            df[c["name"]] = df[c["name"]].astype("boolean")
    if fmt == "csv":
        return df.to_csv(index=False).encode("utf-8")
    if fmt == "json":
        return df.to_json(orient="records", lines=True, date_format="iso").encode("utf-8")
    if fmt == "parquet":
        buf = io.BytesIO()
        df.to_parquet(buf, index=False, engine="pyarrow")
        return buf.getvalue()
    raise ConfigError(f"Unsupported format {fmt}")


def _join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


class AdlsSink:
    def __init__(self, cfg: Config):
        from azure.storage.filedatalake import DataLakeServiceClient

        self.root = cfg.root_path
        self.allow_root_clean = cfg.allow_clean_container_root
        self.label = f"abfss://{cfg.container}@{cfg.account_name}.dfs.core.windows.net/{self.root}"
        svc = DataLakeServiceClient(
            account_url=f"https://{cfg.account_name}.dfs.core.windows.net",
            credential=cfg.access_key(),
        )
        self.fs = svc.get_file_system_client(cfg.container)
        if not self.fs.exists():
            print(f"Container {cfg.container!r} not found - creating it")
            self.fs.create_file_system()

    def clean(self) -> None:
        if self.root:
            d = self.fs.get_directory_client(self.root)
            if d.exists():
                d.delete_directory()  # recursive on ADLS Gen2
                print(f"Deleted {self.label}")
            else:
                print(f"Nothing to clean at {self.label}")
            return
        if not self.allow_root_clean:
            raise ConfigError(
                "adls.root_path is empty, so cleaning would wipe the WHOLE container. "
                "Set a root_path, or set adls.allow_clean_container_root: true."
            )
        n = 0
        for p in self.fs.get_paths(recursive=False):
            if p.is_directory:
                self.fs.get_directory_client(p.name).delete_directory()
            else:
                self.fs.get_file_client(p.name).delete_file()
            n += 1
        print(f"Deleted {n} top-level item(s) from container")

    def write(self, rel_path: str, payload: bytes) -> str:
        path = _join(self.root, rel_path)
        self.fs.get_file_client(path).upload_data(payload, overwrite=True)
        return path


class LocalSink:
    """Same behaviour as ADLS but writes to a local folder (for testing)."""

    def __init__(self, folder: str, cfg: Config):
        self.base = Path(folder).resolve() / cfg.root_path if cfg.root_path else Path(folder).resolve()
        self.label = str(self.base)

    def clean(self) -> None:
        if self.base.exists():
            shutil.rmtree(self.base)
            print(f"Deleted {self.label}")

    def write(self, rel_path: str, payload: bytes) -> str:
        p = self.base / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)
        return str(p)
