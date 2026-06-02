from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
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


# --- OSS upload --------------------------------------------------------
#
# The release artifacts (``fund_data_query.sqlite.gz``,
# ``fund_data_query.sqlite.gz.sha256`` and the manifest) live
# under a per-version directory. The CI workflow runs
# ``cloud upload`` after ``cloud build-bundle`` to copy the
# three files into the bucket and publish the manifest at
# ``current/manifest.json`` so ``cloud pull`` consumers see
# the new version without re-deploying.
#
# We shell out to ``ossutil`` (the official Alibaba Cloud CLI)
# rather than the HTTP SDK because the agent environment
# already has it on PATH with a configured
# ``~/.ossutilconfig`` -- no extra Python dependency, no
# per-machine bootstrap, and the same command works from
# cron / GitHub Actions / a developer laptop.

OSSUTIL_BIN = "ossutil"
DEFAULT_BUCKET = "fund-data-public-l"
DEFAULT_PREFIX = "fund-data"
DEFAULT_REGION = "cn-shanghai"
MANIFEST_URL_ENV = "FUND_DATA_MANIFEST_URL"
AUTO_PULL_ENV = "FUND_DATA_AUTO_PULL"


@dataclass
class UploadResult:
    """Outcome of :func:`upload_to_oss` -- what was pushed, where,
    and the public manifest URL consumers will read.

    Stable JSON schema that the agent CI gate branches on:
    ``version``, ``bucket``, ``region``, ``prefix``, ``manifest_url``,
    ``uploaded`` (list of {local, remote, size_bytes, sha256}).
    """

    version: str
    bucket: str
    region: str
    prefix: str
    manifest_url: str
    uploaded: list[dict[str, Any]]
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "bucket": self.bucket,
            "region": self.region,
            "prefix": self.prefix,
            "manifest_url": self.manifest_url,
            "uploaded": list(self.uploaded),
            "dry_run": self.dry_run,
        }


def default_manifest_url() -> str:
    """Return the project-configured public OSS manifest URL."""
    configured = os.environ.get(MANIFEST_URL_ENV)
    if configured:
        return configured
    base_url = f"https://{DEFAULT_BUCKET}.oss-{DEFAULT_REGION}.aliyuncs.com"
    return f"{base_url}/{DEFAULT_PREFIX}/current/manifest.json"


def ensure_project_bundle(
    *,
    cache_dir: str | Path | None = None,
    manifest_url: str | None = None,
) -> dict[str, Any]:
    """Install the project OSS query bundle when no local DB is pinned.

    Agent entry points call this before provider/API work. A successful
    pull makes ``fund_data.default_db_path()`` resolve to the OSS-backed
    query database. A failed pull returns a structured API fallback
    signal instead of raising, so live providers can still run.
    """
    cache_path = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    if os.environ.get("FUND_DATA_DB") and not (
        cache_dir is not None or os.environ.get("FUND_DATA_CACHE_DIR")
    ):
        return {
            "installed": False,
            "cache_dir": str(cache_path),
            "db_path": None,
            "version": None,
            "manifest_url": manifest_url or default_manifest_url(),
            "source": None,
            "fallback": None,
            "skipped": "FUND_DATA_DB is set",
        }
    if os.environ.get(AUTO_PULL_ENV, "1").strip().lower() in {"0", "false", "no", "off"}:
        return {
            "installed": False,
            "cache_dir": str(cache_path),
            "db_path": None,
            "version": None,
            "manifest_url": manifest_url or default_manifest_url(),
            "source": None,
            "fallback": "api",
            "skipped": f"{AUTO_PULL_ENV} is disabled",
        }

    existing = current_db_path(cache_path)
    if existing:
        result = status(cache_dir=cache_path)
        result.update(
            {
                "manifest_url": result.get("manifest_url") or manifest_url or default_manifest_url(),
                "source": "cache",
                "fallback": None,
                "skipped": "cloud cache already installed",
            }
        )
        return result

    url = manifest_url or default_manifest_url()
    try:
        result = pull_bundle(url, cache_dir=cache_path)
    except Exception as exc:  # noqa: BLE001 - bootstrap failure should fall back to providers
        return {
            "installed": False,
            "cache_dir": str(cache_path),
            "db_path": None,
            "version": None,
            "manifest_url": url,
            "source": None,
            "fallback": "api",
            "error": str(exc),
        }
    result.update({"source": "oss", "fallback": None, "skipped": None})
    return result


def _ossutil_upload(local: Path, remote: str, *, dry_run: bool = False) -> None:
    """Invoke ``ossutil cp -f local remote`` and raise a clear
    error if the upload does not finish. ``-f`` is required --
    without it ossutil prompts "y or N" on existing keys, which
    hangs the non-interactive shell."""
    cmd = [OSSUTIL_BIN, "cp", "-f", str(local), remote]
    if dry_run:
        return
    result = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ossutil upload failed ({result.returncode}): {result.stderr.strip()}"
        )


def upload_to_oss(
    *,
    release_dir: str | Path,
    bucket: str = DEFAULT_BUCKET,
    region: str = DEFAULT_REGION,
    prefix: str = DEFAULT_PREFIX,
    manifest_path: str | Path | None = None,
    dry_run: bool = False,
) -> UploadResult:
    """Upload a ``cloud build-bundle`` release to OSS.

    Pushes the gzip db + sha256 into ``{prefix}/releases/{version}/``
    and (when ``manifest_path`` is provided) the manifest into
    ``{prefix}/current/manifest.json``. Returns an
    :class:`UploadResult` with the public manifest URL.

    Parameters
    ----------
    release_dir:
        The directory that ``build_bundle`` produced -- must
        contain ``fund_data_query.sqlite.gz`` and the matching
        ``.sha256`` sidecar.
    bucket:
        OSS bucket name. Defaults to ``fund-data-public-l``.
    region:
        OSS region. Defaults to ``cn-shanghai``.
    prefix:
        Object key prefix. The release goes under
        ``{prefix}/releases/{version}/`` and the manifest
        under ``{prefix}/current/manifest.json``.
    manifest_path:
        Path to a manifest.json to publish at
        ``{prefix}/current/manifest.json``. When ``None`` the
        manifest is not republished -- pass it to keep the
        ``current/`` pointer in sync with the just-uploaded
        release.
    dry_run:
        Skip the ossutil calls and just return the planned
        ``uploaded`` list. Useful in CI to preview what would
        land in OSS.
    """
    release_path = Path(release_dir)
    if not release_path.is_dir():
        raise FileNotFoundError(f"release directory does not exist: {release_path}")
    # We upload the *gzipped* db, not the raw .sqlite. The raw
    # file (852 MB) is a build intermediate kept around for
    # local debugging -- the public artifact is the 121 MB gz
    # that the manifest already points consumers at.
    archive_path = release_path / QUERY_ARCHIVE_NAME
    sha_path = release_path / f"{QUERY_ARCHIVE_NAME}.sha256"
    if not archive_path.is_file():
        raise FileNotFoundError(f"missing query db archive: {archive_path}")
    if not sha_path.is_file():
        raise FileNotFoundError(f"missing sha256 sidecar: {sha_path}")

    # The release id is the directory name. build_bundle
    # writes ``{version}/`` to keep every release immutable --
    # an updated dataset lives under a new directory and the
    # current/ pointer is republished.
    version = _safe_version(release_path.name)
    release_prefix = f"{prefix}/releases/{version}"
    archive_remote = f"oss://{bucket}/{release_prefix}/{QUERY_ARCHIVE_NAME}"
    sha_remote = f"oss://{bucket}/{release_prefix}/{QUERY_ARCHIVE_NAME}.sha256"
    base_url = f"https://{bucket}.oss-{region}.aliyuncs.com"

    uploaded: list[dict[str, Any]] = []
    for local, remote in ((archive_path, archive_remote), (sha_path, sha_remote)):
        _ossutil_upload(local, remote, dry_run=dry_run)
        uploaded.append(
            {
                "local": str(local),
                "remote": remote,
                "size_bytes": local.stat().st_size,
            }
        )

    manifest_url = ""
    if manifest_path is not None:
        manifest_remote = f"oss://{bucket}/{prefix}/current/manifest.json"
        _ossutil_upload(Path(manifest_path), manifest_remote, dry_run=dry_run)
        uploaded.append(
            {
                "local": str(manifest_path),
                "remote": manifest_remote,
                "size_bytes": Path(manifest_path).stat().st_size,
            }
        )
        manifest_url = f"{base_url}/{prefix}/current/manifest.json"
    else:
        manifest_url = (
            f"{base_url}/{prefix}/releases/{version}/manifest.json"
        )

    return UploadResult(
        version=version,
        bucket=bucket,
        region=region,
        prefix=prefix,
        manifest_url=manifest_url,
        uploaded=uploaded,
        dry_run=dry_run,
    )
