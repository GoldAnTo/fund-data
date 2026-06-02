from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SCHEMA_VERSION = 1
QUERY_DB_NAME = "fund_data_query.sqlite"
QUERY_ARCHIVE_NAME = f"{QUERY_DB_NAME}.gz"
FULL_DB_NAME = "fund_data_full.sqlite"
FULL_ARCHIVE_NAME = f"{FULL_DB_NAME}.gz"
MANIFEST_NAME = "manifest.json"
CURRENT_METADATA_NAME = "current.json"

QUERY_TABLES = (
    "funds",
    "nav_history",
    "snapshots",
    "stock_holdings",
    "fund_profiles",
    "bond_holdings",
    "industry_allocations",
    "fee_structures",
    "dividends",
    "splits",
    "fund_managers",
)
EXCLUDED_TABLES = (
    "raw_responses",
    "sync_runs",
    "sync_failures",
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def default_cache_dir() -> Path:
    return Path(os.environ.get("FUND_DATA_CACHE_DIR") or Path.home() / ".cache" / "fund-data")


def current_db_path(cache_dir: str | Path | None = None) -> Path | None:
    cache_path = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    metadata_path = cache_path / CURRENT_METADATA_NAME
    if not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    db_value = metadata.get("db_path")
    if not db_value:
        return None
    db_path = Path(db_value)
    return db_path if db_path.is_file() else None


def build_bundle(
    *,
    source_db: str | Path,
    output_dir: str | Path,
    base_url: str,
    version: str | None = None,
    manifest_output: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(source_db)
    if not source_path.is_file():
        raise FileNotFoundError(f"source database does not exist: {source_path}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    query_db_path = output_path / QUERY_DB_NAME
    archive_path = output_path / QUERY_ARCHIVE_NAME
    sha_path = output_path / f"{QUERY_ARCHIVE_NAME}.sha256"
    manifest_path = Path(manifest_output) if manifest_output else output_path / MANIFEST_NAME

    for artifact in (query_db_path, archive_path, sha_path):
        if artifact.exists():
            artifact.unlink()

    copied_tables = _build_query_database(source_path, query_db_path)
    _gzip_file(query_db_path, archive_path)
    digest = _sha256_file(archive_path)
    sha_path.write_text(f"{digest}  {QUERY_ARCHIVE_NAME}\n", encoding="utf-8")

    base = base_url.rstrip("/") + "/"
    manifest = {
        "kind": "fund-data-cloud-bundle",
        "version": version or datetime.now(UTC).strftime("%Y-%m-%d"),
        "schema_version": SCHEMA_VERSION,
        "updated_at": utc_now(),
        "files": {
            "query_db": {
                "path": QUERY_ARCHIVE_NAME,
                "url": _join_location(base, QUERY_ARCHIVE_NAME),
                "sha256": digest,
                "size_bytes": archive_path.stat().st_size,
                "uncompressed_size_bytes": query_db_path.stat().st_size,
                "compression": "gzip",
            }
        },
        "tables": copied_tables,
        "excluded_tables": list(EXCLUDED_TABLES),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "query_db_path": query_db_path,
        "query_archive_path": archive_path,
        "sha256_path": sha_path,
    }


def archive_full(
    *,
    source_db: str | Path,
    output_dir: str | Path,
    base_url: str | None = None,
    version: str | None = None,
    manifest_output: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(source_db)
    if not source_path.is_file():
        raise FileNotFoundError(f"source database does not exist: {source_path}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    full_db_path = output_path / FULL_DB_NAME
    archive_path = output_path / FULL_ARCHIVE_NAME
    sha_path = output_path / f"{FULL_ARCHIVE_NAME}.sha256"
    manifest_path = Path(manifest_output) if manifest_output else output_path / MANIFEST_NAME

    for artifact in (full_db_path, archive_path, sha_path):
        if artifact.exists():
            artifact.unlink()

    _copy_sqlite_snapshot(source_path, full_db_path)
    table_counts = _table_counts(full_db_path)
    _gzip_file(full_db_path, archive_path)
    digest = _sha256_file(archive_path)
    sha_path.write_text(f"{digest}  {FULL_ARCHIVE_NAME}\n", encoding="utf-8")

    file_entry: dict[str, Any] = {
        "path": FULL_ARCHIVE_NAME,
        "url": None,
        "oss_uri": None,
        "sha256": digest,
        "size_bytes": archive_path.stat().st_size,
        "uncompressed_size_bytes": full_db_path.stat().st_size,
        "compression": "gzip",
    }
    if base_url:
        base = base_url.rstrip("/") + "/"
        if base.startswith("oss://"):
            file_entry["oss_uri"] = _join_location(base, FULL_ARCHIVE_NAME)
        else:
            file_entry["url"] = _join_location(base, FULL_ARCHIVE_NAME)

    manifest = {
        "kind": "fund-data-full-archive",
        "version": version or datetime.now(UTC).strftime("%Y-%m-%d"),
        "schema_version": SCHEMA_VERSION,
        "updated_at": utc_now(),
        "files": {"full_db": file_entry},
        "tables": table_counts,
        "privacy": "private",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "full_db_path": full_db_path,
        "full_archive_path": archive_path,
        "sha256_path": sha_path,
    }


def pull_bundle(
    manifest_url: str,
    *,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    manifest = json.loads(_read_bytes(manifest_url).decode("utf-8"))
    _validate_manifest(manifest)

    file_info = manifest["files"]["query_db"]
    archive_url = file_info.get("url") or urljoin(
        _manifest_base_url(manifest_url), file_info["path"]
    )
    expected_sha = str(file_info["sha256"]).lower()
    version = str(manifest["version"])

    cache_path = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    release_dir = cache_path / "releases" / _safe_version(version)
    release_dir.mkdir(parents=True, exist_ok=True)

    archive_path = release_dir / QUERY_ARCHIVE_NAME
    archive_tmp = release_dir / f"{QUERY_ARCHIVE_NAME}.download"
    db_path = release_dir / QUERY_DB_NAME
    db_tmp = release_dir / f"{QUERY_DB_NAME}.download"

    _download(archive_url, archive_tmp)
    actual_sha = _sha256_file(archive_tmp)
    if actual_sha.lower() != expected_sha:
        archive_tmp.unlink(missing_ok=True)
        raise ValueError(f"sha256 mismatch for query_db: expected {expected_sha}, got {actual_sha}")

    _gunzip_file(archive_tmp, db_tmp)
    os.replace(archive_tmp, archive_path)
    os.replace(db_tmp, db_path)

    metadata = {
        "version": version,
        "schema_version": manifest["schema_version"],
        "installed_at": utc_now(),
        "manifest_url": manifest_url,
        "manifest": manifest,
        "db_path": str(db_path),
        "archive_path": str(archive_path),
        "sha256": expected_sha,
        "size_bytes": archive_path.stat().st_size,
        "db_size_bytes": db_path.stat().st_size,
    }
    _write_json_atomic(cache_path / CURRENT_METADATA_NAME, metadata)
    return status(cache_dir=cache_path)


def status(
    *,
    cache_dir: str | Path | None = None,
    manifest_url: str | None = None,
) -> dict[str, Any]:
    cache_path = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    metadata_path = cache_path / CURRENT_METADATA_NAME
    if not metadata_path.exists():
        result: dict[str, Any] = {
            "installed": False,
            "cache_dir": str(cache_path),
            "db_path": None,
            "version": None,
        }
        if manifest_url:
            result.update(_remote_status(manifest_url))
        return result

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    db_path = Path(metadata.get("db_path", ""))
    installed = db_path.is_file()
    result = {
        "installed": installed,
        "cache_dir": str(cache_path),
        "version": metadata.get("version"),
        "schema_version": metadata.get("schema_version"),
        "installed_at": metadata.get("installed_at"),
        "manifest_url": metadata.get("manifest_url"),
        "db_path": str(db_path) if metadata.get("db_path") else None,
        "sha256": metadata.get("sha256"),
        "size_bytes": metadata.get("size_bytes"),
        "db_size_bytes": db_path.stat().st_size if installed else None,
    }
    if manifest_url:
        remote = _remote_status(manifest_url)
        result.update(remote)
        result["update_available"] = bool(remote.get("remote_version") != result["version"])
    return result


def json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    return value


def _build_query_database(source_db: Path, query_db: Path) -> dict[str, int]:
    copied: dict[str, int] = {}
    with closing(sqlite3.connect(source_db)) as source:
        table_sql = dict(
            source.execute(
                "select name, sql from sqlite_master where type = 'table' and sql is not null"
            ).fetchall()
        )

    with closing(sqlite3.connect(query_db)) as dest:
        dest.execute("pragma journal_mode = OFF")
        dest.execute("pragma synchronous = OFF")
        dest.execute("pragma temp_store = MEMORY")
        dest.execute("attach database ? as source", (str(source_db),))
        try:
            for table in QUERY_TABLES:
                sql = table_sql.get(table)
                if not sql:
                    continue
                dest.execute(sql)
                table_name = _quote_identifier(table)
                dest.execute(f"insert into {table_name} select * from source.{table_name}")
                copied[table] = dest.execute(f"select count(*) from {table_name}").fetchone()[0]
            _create_query_indexes(dest, copied.keys())
            dest.commit()
        finally:
            dest.execute("detach database source")
        dest.execute("analyze")
        dest.execute("pragma optimize")
        dest.execute("vacuum")
    return copied


def _copy_sqlite_snapshot(source_db: Path, dst_db: Path) -> None:
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    if dst_db.exists():
        dst_db.unlink()
    source_uri = f"file:{source_db}?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True, timeout=30.0)) as source,
        closing(sqlite3.connect(dst_db, timeout=30.0)) as target,
    ):
        source.backup(target)


def _table_counts(db_path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(db_path)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
            ).fetchall()
        ]
        return {
            table: conn.execute(f"select count(*) from {_quote_identifier(table)}").fetchone()[0]
            for table in sorted(tables)
        }


def _create_query_indexes(conn: sqlite3.Connection, table_names: Any) -> None:
    tables = set(table_names)
    if "funds" in tables:
        conn.execute("create index if not exists idx_funds_fund_name on funds(fund_name)")
        conn.execute("create index if not exists idx_funds_fund_type on funds(fund_type)")
        conn.execute("create index if not exists idx_funds_company on funds(company)")
    if "nav_history" in tables:
        conn.execute("create index if not exists idx_nav_history_nav_date on nav_history(nav_date)")
    if "fund_profiles" in tables:
        conn.execute(
            "create index if not exists idx_fund_profiles_company on fund_profiles(fund_company)"
        )
    if "fund_managers" in tables:
        conn.execute(
            "create index if not exists idx_fund_managers_current_codes "
            "on fund_managers(current_fund_codes)"
        )


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("kind") != "fund-data-cloud-bundle":
        raise ValueError("manifest kind must be fund-data-cloud-bundle")
    if not manifest.get("version"):
        raise ValueError("manifest version is required")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version: {manifest.get('schema_version')}; expected {SCHEMA_VERSION}"
        )
    file_info = manifest.get("files", {}).get("query_db")
    if not isinstance(file_info, dict):
        raise ValueError("manifest files.query_db is required")
    if not file_info.get("sha256"):
        raise ValueError("manifest files.query_db.sha256 is required")
    if not (file_info.get("url") or file_info.get("path")):
        raise ValueError("manifest files.query_db.url or path is required")


def _remote_status(manifest_url: str) -> dict[str, Any]:
    manifest = json.loads(_read_bytes(manifest_url).decode("utf-8"))
    _validate_manifest(manifest)
    query_file = manifest["files"]["query_db"]
    return {
        "remote_version": manifest.get("version"),
        "remote_updated_at": manifest.get("updated_at"),
        "remote_schema_version": manifest.get("schema_version"),
        "remote_query_db_size_bytes": query_file.get("size_bytes"),
        "remote_query_db_sha256": query_file.get("sha256"),
    }


def _gzip_file(src: Path, dst: Path) -> None:
    with src.open("rb") as source, gzip.open(dst, "wb", compresslevel=9) as target:
        shutil.copyfileobj(source, target)


def _gunzip_file(src: Path, dst: Path) -> None:
    if dst.exists():
        dst.unlink()
    with gzip.open(src, "rb") as source, dst.open("wb") as target:
        shutil.copyfileobj(source, target)


def _download(location: str, dst: Path) -> None:
    if dst.exists():
        dst.unlink()
    with _open_location(location) as source, dst.open("wb") as target:
        shutil.copyfileobj(source, target)


def _read_bytes(location: str) -> bytes:
    with _open_location(location) as response:
        return response.read()


def _open_location(location: str):
    parsed = urlparse(location)
    if parsed.scheme in {"http", "https", "file"}:
        request = Request(location, headers={"User-Agent": "fund-data-cloud/0.1"})
        return urlopen(request, timeout=60)
    return Path(location).open("rb")


def _manifest_base_url(manifest_url: str) -> str:
    parsed = urlparse(manifest_url)
    if parsed.scheme:
        return manifest_url.rsplit("/", 1)[0] + "/"
    return str(Path(manifest_url).resolve().parent.as_uri()) + "/"


def _join_location(base: str, name: str) -> str:
    if base.startswith("oss://"):
        return base.rstrip("/") + "/" + name.lstrip("/")
    return urljoin(base.rstrip("/") + "/", name.lstrip("/"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _safe_version(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "current"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
