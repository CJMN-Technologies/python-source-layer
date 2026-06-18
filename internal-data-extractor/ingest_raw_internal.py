import logging
import os
import re
import shutil
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql
from psycopg2.extras import execute_batch


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data/new_raw"
READ_DATA_DIR = SCRIPT_DIR / "data/read_data"
INTERNAL_DATA_DIR = DATA_DIR / "Internal"
EXTERNAL_DATA_DIR = DATA_DIR / "External"
INTERNAL_ARCHIVE_DIR = READ_DATA_DIR / "Internal"
EXTERNAL_ARCHIVE_DIR = READ_DATA_DIR / "External"
DEFAULT_SSL_ROOT_CERT = SCRIPT_DIR / "certs/prod-ca-2021.crt"
SCHEMA_NAME = "AFCS"
TABLE_PREFIX = "ridership"
BATCH_SIZE = 500

LOGGER = logging.getLogger("secure_lrt_ingest")

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

PERIOD_ORDER = {
    "Daily Total": 0,
    "7-9am (AM PEAK)": 1,
    "5-7pm (PM PEAK)": 2,
    "5-7am (OFF PEAK)": 3,
    "9am-5pm (OFF PEAK)": 4,
    "7-10pm (OFF PEAK)": 5,
    "7-11pm (OFF PEAK)": 6,
    "Peak Total": 20,
    "Off-Peak Total": 21,
    "Monthly Total": 22,
}

STATION_ALIASES = {
    "betty_go": "betty_go_belmonte",
    "betty_go_belmonte": "betty_go_belmonte",
    "araneta_cubao": "araneta_center_cubao",
    "araneta_center_cubao": "araneta_center_cubao",
    "marikina": "marikina_pasig",
    "marikina_pasiq": "marikina_pasig",
    "marikina_pasig": "marikina_pasig",
    "total": "total",
}


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


@dataclass(frozen=True)
class SourceFile:
    path: Path
    year: int
    month: int


@dataclass
class RidershipRow:
    service_date: date
    time_period: str
    values: Dict[str, Optional[int]]
    sort_key: Tuple[int, int, date]


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


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
        "application_name": "secure_lrt_ingest",
    }


def normalize_identifier(value: Any) -> str:
    normalized = str(value).strip().lower()
    normalized = normalized.replace("\n", " ")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return STATION_ALIASES.get(normalized, normalized)


def parse_source_file(path: Path) -> Optional[SourceFile]:
    if path.name.startswith("~$") or path.suffix.lower() not in {".csv", ".xlsx"}:
        return None
    if "afcs" not in path.stem.lower():
        return None

    match = re.search(
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|"
        r"dec(?:ember)?)[^0-9]*(20\d{2})",
        path.stem,
        re.IGNORECASE,
    )
    if not match:
        return None

    month = MONTHS[match.group(1).lower()]
    year = int(match.group(2))
    return SourceFile(path=path, year=year, month=month)


def discover_input_files(data_dir: Path) -> List[SourceFile]:
    sources: List[SourceFile] = []
    if not data_dir.exists():
        return sources

    candidates = [
        path
        for path in data_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".csv", ".xlsx"}
    ]
    for path in sorted(candidates):
        source = parse_source_file(path)
        if source:
            sources.append(source)
    return sorted(sources, key=lambda item: (item.year, item.month, item.path.name.lower()))


def archive_source_file(source_path: Path) -> None:
    archive_pending_file(source_path, INTERNAL_ARCHIVE_DIR)


def read_raw_sheet(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path, header=None)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, header=None)
    raise ValueError("Unsupported input file type.")


def period_label(raw_label: Any) -> Optional[str]:
    if pd.isna(raw_label):
        return None

    label = str(raw_label).strip()
    normalized = re.sub(r"\s+", " ", label).lower()
    if "7-9am" in normalized and "peak" in normalized:
        return "7-9am (AM PEAK)"
    if "5-7pm" in normalized and "peak" in normalized:
        return "5-7pm (PM PEAK)"
    if "5-7am" in normalized and "off" in normalized:
        return "5-7am (OFF PEAK)"
    if "9am-5pm" in normalized and "off" in normalized:
        return "9am-5pm (OFF PEAK)"
    if "7-10pm" in normalized and "off" in normalized:
        return "7-10pm (OFF PEAK)"
    if "7-11pm" in normalized and "off" in normalized:
        return "7-11pm (OFF PEAK)"
    if "daily" in normalized and "total" in normalized:
        return "Daily Total"
    return None


def row_period_label(raw_label: Any) -> Optional[str]:
    if pd.isna(raw_label):
        return None

    label = str(raw_label).strip()
    normalized = re.sub(r"\s+", "", label).lower()
    if normalized in {"7-9am", "07:00-09:00", "0700-0900"}:
        return "7-9am (AM PEAK)"
    if normalized in {"5-7pm", "17:00-19:00", "1700-1900"}:
        return "5-7pm (PM PEAK)"
    if normalized in {"5-7am", "05:00-07:00", "0500-0700"}:
        return "5-7am (OFF PEAK)"
    if normalized in {"9am-5pm", "09:00-17:00", "0900-1700"}:
        return "9am-5pm (OFF PEAK)"
    if normalized in {"7-10pm", "19:00-22:00", "1900-2200"}:
        return "7-10pm (OFF PEAK)"
    if normalized in {"7-11pm", "19:00-23:00", "1900-2300"}:
        return "7-11pm (OFF PEAK)"
    if normalized in {"subtotal(peak)", "peaktotal"}:
        return "Peak Total"
    if normalized in {"subtotal(offpeak)", "offpeaktotal", "off-peaktotal"}:
        return "Off-Peak Total"
    if normalized in {"total", "total(peak+offpeak)", "monthlytotal"}:
        return "Monthly Total"
    if re.fullmatch(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}", normalized):
        return label
    return period_label(label)


def period_order(label: str) -> int:
    if label in PERIOD_ORDER:
        return PERIOD_ORDER[label]

    match = re.match(r"^(\d{1,2}):(\d{2})-", label)
    if match:
        return 100 + int(match.group(1)) * 60 + int(match.group(2))
    return 10000


def station_row_index(raw: pd.DataFrame, start_at: int) -> Optional[int]:
    for index in range(start_at, len(raw)):
        row = raw.iloc[index]
        first_cell = row.iloc[0]
        if isinstance(first_cell, str) and first_cell.strip().lower() == "station":
            return index
    return None


def block_period(raw: pd.DataFrame, station_index: int) -> str:
    for index in range(station_index - 1, max(-1, station_index - 6), -1):
        for value in raw.iloc[index].dropna().tolist():
            label = period_label(value)
            if label:
                return label
    raise ValueError("Unable to identify time period for a data block.")


def build_column_map(raw: pd.DataFrame, station_index: int) -> Dict[int, str]:
    station_row = raw.iloc[station_index]
    entry_exit_row = raw.iloc[station_index + 1]
    column_map: Dict[int, str] = {}
    current_station: Optional[str] = None

    for column_index in range(2, len(station_row)):
        station_value = station_row.iloc[column_index]
        if not pd.isna(station_value):
            current_station = normalize_identifier(station_value)

        metric_value = entry_exit_row.iloc[column_index]
        metric = normalize_identifier(metric_value) if not pd.isna(metric_value) else ""
        if current_station and metric in {"entry", "exit"}:
            column_map[column_index] = f"{current_station}_{metric}"

    if not column_map:
        raise ValueError("Unable to map station entry and exit columns.")
    return column_map


def build_alternating_column_map(raw: pd.DataFrame, station_index: int) -> Dict[int, str]:
    station_row = raw.iloc[station_index]
    column_map: Dict[int, str] = {}
    current_station: Optional[str] = None
    metric = "entry"

    for column_index in range(1, len(station_row)):
        station_value = station_row.iloc[column_index]
        if not pd.isna(station_value):
            current_station = normalize_identifier(station_value)
            metric = "entry"

        if current_station:
            column_map[column_index] = f"{current_station}_{metric}"
            metric = "exit" if metric == "entry" else "entry"

    if not column_map:
        raise ValueError("Unable to map alternating station entry and exit columns.")
    return column_map


def parse_service_date(value: Any, source: SourceFile) -> Optional[date]:
    if pd.isna(value):
        return None

    parsed = pd.to_datetime(f"{value}-{source.year}", errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return date(source.year, int(parsed.month), int(parsed.day))


def optional_int(value: Any) -> Optional[int]:
    if pd.isna(value):
        return None

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned or not any(character.isdigit() for character in cleaned):
            return None
        value = cleaned

    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if pd.isna(parsed) or int(parsed) != parsed or parsed < 0:
        raise ValueError("Invalid ridership integer field.")
    return int(parsed)


def parse_period_rows(
    raw: pd.DataFrame,
    source: SourceFile,
    station_index: int,
) -> List[RidershipRow]:
    period = block_period(raw, station_index)
    columns = build_column_map(raw, station_index)
    rows: List[RidershipRow] = []

    index = station_index + 2
    while index < len(raw):
        first_cell = raw.iloc[index, 0]
        if isinstance(first_cell, str) and first_cell.strip().lower() == "station":
            break
        if period_label(first_cell):
            break

        service_date = parse_service_date(first_cell, source)
        if service_date:
            values = {
                column_name: optional_int(raw.iloc[index, column_index])
                for column_index, column_name in columns.items()
            }
            rows.append(
                RidershipRow(
                    service_date=service_date,
                    time_period=period,
                    values=values,
                    sort_key=(source.month, period_order(period), service_date),
                )
            )
        index += 1

    return rows


def is_monthly_summary(raw: pd.DataFrame, station_index: int) -> bool:
    if station_index + 1 >= len(raw):
        return False
    first_header = raw.iloc[station_index + 1, 0]
    return isinstance(first_header, str) and normalize_identifier(first_header) == "time"


def parse_monthly_summary(source: SourceFile, raw: pd.DataFrame, station_index: int) -> List[RidershipRow]:
    rows: List[RidershipRow] = []
    month_date = date(source.year, source.month, 1)

    time_columns = build_alternating_column_map(raw, station_index)
    index = station_index + 2
    while index < len(raw):
        label = row_period_label(raw.iloc[index, 0])
        if not label:
            break
        values = {
            column_name: optional_int(raw.iloc[index, column_index])
            for column_index, column_name in time_columns.items()
        }
        rows.append(
            RidershipRow(
                service_date=month_date,
                time_period=label,
                values=values,
                sort_key=(source.month, period_order(label), month_date),
            )
        )
        index += 1

    for summary_index in range(index, len(raw)):
        first_cell = raw.iloc[summary_index, 0]
        if isinstance(first_cell, str) and normalize_identifier(first_cell) == "peak_hours":
            summary_columns = build_alternating_column_map(raw, summary_index)
            row_index = summary_index + 1
            while row_index < len(raw):
                label = row_period_label(raw.iloc[row_index, 0])
                if not label:
                    break
                values = {
                    column_name: optional_int(raw.iloc[row_index, column_index])
                    for column_index, column_name in summary_columns.items()
                }
                rows.append(
                    RidershipRow(
                        service_date=month_date,
                        time_period=label,
                        values=values,
                        sort_key=(source.month, period_order(label), month_date),
                    )
                )
                row_index += 1
            break

    if not rows:
        raise ValueError("No monthly summary rows were parsed from the source file.")
    return rows


def parse_file(source: SourceFile) -> List[RidershipRow]:
    raw = read_raw_sheet(source.path)
    rows: List[RidershipRow] = []
    cursor = 0

    while True:
        station_index = station_row_index(raw, cursor)
        if station_index is None:
            break
        if is_monthly_summary(raw, station_index):
            return parse_monthly_summary(source, raw, station_index)
        rows.extend(parse_period_rows(raw, source, station_index))
        cursor = station_index + 2

    if not rows:
        raise ValueError("No ridership rows were parsed from the source file.")
    return with_daily_totals(rows)


def with_daily_totals(rows: Sequence[RidershipRow]) -> List[RidershipRow]:
    totals: Dict[date, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen_columns: Dict[date, set[str]] = defaultdict(set)

    for row in rows:
        for column, value in row.values.items():
            if value is not None:
                totals[row.service_date][column] += value
                seen_columns[row.service_date].add(column)

    daily_rows = [
        RidershipRow(
            service_date=service_date,
            time_period="Daily Total",
            values={
                column: totals[service_date][column]
                for column in sorted(seen_columns[service_date])
            },
            sort_key=(service_date.month, period_order("Daily Total"), service_date),
        )
        for service_date in sorted(totals)
    ]

    return sorted([*daily_rows, *rows], key=lambda row: row.sort_key)


def table_name_for_year(year: int) -> str:
    return f"{TABLE_PREFIX}_{year}"


def ridership_columns(rows: Sequence[RidershipRow]) -> List[str]:
    columns = sorted({column for row in rows for column in row.values})
    return [column for column in columns if column != "total_entry" and column != "total_exit"] + [
        column for column in ("total_entry", "total_exit") if column in columns
    ]


def create_schema(cursor: psycopg2.extensions.cursor) -> None:
    cursor.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}").format(
            schema=sql.Identifier(SCHEMA_NAME)
        )
    )
    cursor.execute(
        sql.SQL("REVOKE ALL ON SCHEMA {schema} FROM PUBLIC").format(
            schema=sql.Identifier(SCHEMA_NAME)
        )
    )


def create_year_table(
    cursor: psycopg2.extensions.cursor,
    year: int,
    station_columns: Sequence[str],
) -> None:
    table = sql.Identifier(SCHEMA_NAME, table_name_for_year(year))
    ridership_defs = [
        sql.SQL("{} INTEGER CHECK ({} IS NULL OR {} >= 0)").format(
            sql.Identifier(column),
            sql.Identifier(column),
            sql.Identifier(column),
        )
        for column in station_columns
    ]

    cursor.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                date DATE NOT NULL,
                time_period TEXT NOT NULL CHECK (length(time_period) <= 64),
                {ridership_columns},
                load_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ).format(table=table, ridership_columns=sql.SQL(",\n                ").join(ridership_defs))
    )

    for column in station_columns:
        cursor.execute(
            sql.SQL(
                "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} "
                "INTEGER CHECK ({column} IS NULL OR {column} >= 0)"
            ).format(table=table, column=sql.Identifier(column))
        )

    cursor.execute(sql.SQL("REVOKE ALL ON TABLE {table} FROM PUBLIC").format(table=table))


def build_insert_records(
    year: int,
    rows: Sequence[RidershipRow],
    station_columns: Sequence[str],
) -> List[Tuple[Any, ...]]:
    records: List[Tuple[Any, ...]] = []
    for sequence, row in enumerate(rows, start=1):
        record_id = f"YR{year}-{sequence:04d}"
        records.append(
            (
                record_id,
                row.service_date,
                row.time_period,
                *(row.values.get(column) for column in station_columns),
            )
        )
    return records


def insert_year_rows(
    cursor: psycopg2.extensions.cursor,
    year: int,
    rows: Sequence[RidershipRow],
    station_columns: Sequence[str],
) -> None:
    records = build_insert_records(year, rows, station_columns)
    if not records:
        return

    columns = ["id", "date", "time_period", *station_columns]
    statement = sql.SQL(
        """
        INSERT INTO {table} ({columns})
        VALUES ({placeholders})
        ON CONFLICT (id) DO NOTHING
        """
    ).format(
        table=sql.Identifier(SCHEMA_NAME, table_name_for_year(year)),
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        placeholders=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    execute_batch(cursor, statement.as_string(cursor), records, page_size=BATCH_SIZE)


def ingest_files(sources: Iterable[SourceFile]) -> None:
    sources_by_year: Dict[int, List[SourceFile]] = defaultdict(list)
    for source in sources:
        sources_by_year[source.year].append(source)

    if not sources_by_year:
        LOGGER.info("No input files found.")
        return

    with closing(psycopg2.connect(**connection_kwargs())) as connection:
        connection.autocommit = False
        with connection.cursor() as cursor:
            create_schema(cursor)
            connection.commit()

            failed_years: List[int] = []
            for year in sorted(sources_by_year):
                year_sources = sorted(sources_by_year[year], key=lambda item: (item.month, item.path.name.lower()))
                LOGGER.info("Processing year %s with %s file(s).", year, len(year_sources))

                try:
                    rows: List[RidershipRow] = []
                    for position, source in enumerate(year_sources, start=1):
                        LOGGER.info(
                            "Reading file %s of %s for year %s. type=%s",
                            position,
                            len(year_sources),
                            year,
                            source.path.suffix.lower().lstrip("."),
                        )
                        rows.extend(parse_file(source))

                    rows = sorted(rows, key=lambda row: row.sort_key)
                    station_columns = ridership_columns(rows)
                    create_year_table(cursor, year, station_columns)
                    insert_year_rows(cursor, year, rows, station_columns)
                    connection.commit()
                    for source in year_sources:
                        archive_source_file(source.path)
                    LOGGER.info("Year %s completed successfully.", year)
                except Exception as error:
                    connection.rollback()
                    LOGGER.error(
                        "Year %s failed securely. error_type=%s",
                        year,
                        safe_error_code(error),
                    )
                    failed_years.append(year)

            if failed_years:
                raise RuntimeError("One or more years failed during ingestion.")


def main() -> int:
    configure_logging()
    try:
        ingest_files(discover_input_files(DATA_DIR))
    except Exception as error:
        LOGGER.error("Ingestion stopped securely. error_type=%s", safe_error_code(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
