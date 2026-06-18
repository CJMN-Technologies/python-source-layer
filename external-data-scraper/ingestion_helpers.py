import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data/new_raw"
READ_DATA_DIR = SCRIPT_DIR / "data/read_data"
EXTERNAL_DATA_DIR = DATA_DIR / "External"
EXTERNAL_ARCHIVE_DIR = READ_DATA_DIR / "External"
DEFAULT_SSL_ROOT_CERT = SCRIPT_DIR / "certs/prod-ca-2021.crt"


def path_contains_folder(path: Path, folder_name: str) -> bool:
    expected = folder_name.lower()
    return any(expected in part.lower() for part in path.parts)


def pending_named_files(filename: str, preferred_folder: str) -> List[Path]:
    if not DATA_DIR.exists():
        return []

    matches = [
        path
        for path in DATA_DIR.rglob("*")
        if path.is_file()
        and path.name.lower() == filename.lower()
        and not path.name.startswith("~$")
    ]
    return sorted(
        matches,
        key=lambda path: (
            0 if path_contains_folder(path, preferred_folder) else 1,
            len(path.parts),
            str(path).lower(),
        ),
    )


def pending_named_file(filename: str, preferred_folder: str) -> Optional[Path]:
    matches = pending_named_files(filename, preferred_folder)
    return matches[0] if matches else None


def archive_pending_file(source_path: Path, archive_folder: Path) -> None:
    destination = archive_folder / source_path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    shutil.move(str(source_path), str(destination))


def safe_error_code(error: BaseException) -> str:
    pg_code = getattr(error, "pgcode", None)
    if pg_code:
        return f"{error.__class__.__name__}:{pg_code}"
    return error.__class__.__name__


def parse_database_url(database_url: str) -> Dict[str, Any]:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("DATABASE_URL must use a PostgreSQL URL scheme.")
    if not parsed.hostname or not parsed.username or parsed.password is None:
        raise RuntimeError("DATABASE_URL is missing required connection fields.")

    database_name = unquote((parsed.path or "").lstrip("/"))
    if not database_name:
        raise RuntimeError("DATABASE_URL is missing the database name.")

    return {
        "dbname": database_name,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password),
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "options": parse_qs(parsed.query),
    }


def connection_kwargs() -> Dict[str, Any]:
    load_dotenv(dotenv_path=SCRIPT_DIR / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Missing required DATABASE_URL environment variable.")

    parsed = parse_database_url(database_url)
    ssl_root_cert = (
        os.getenv("PGSSLROOTCERT")
        or os.getenv("SUPABASE_SSL_ROOT_CERT")
        or str(DEFAULT_SSL_ROOT_CERT)
    )
    if not Path(ssl_root_cert).exists():
        raise RuntimeError("Missing Supabase SSL root certificate file.")

    return {
        "dbname": parsed["dbname"],
        "user": parsed["user"],
        "password": parsed["password"],
        "host": parsed["host"],
        "port": parsed["port"],
        "sslmode": "verify-full",
        "sslrootcert": ssl_root_cert,
        "connect_timeout": 15,
        "application_name": "external_lrt_ingest",
    }
