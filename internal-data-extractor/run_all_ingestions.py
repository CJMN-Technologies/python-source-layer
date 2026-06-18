import logging
import subprocess
import sys
from pathlib import Path
from typing import Sequence


LOGGER = logging.getLogger("master_ingestion")

SCRIPT_DIR = Path(__file__).resolve().parent

SCRIPTS: Sequence[Path] = (
    SCRIPT_DIR / "ingest_raw_internal.py",
    SCRIPT_DIR / "ingest_student_transaction.py",
    SCRIPT_DIR / "ingest_station_platform_capacity.py",
    SCRIPT_DIR / "ingest_psor_incidents.py",
)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def run_script(script_path: Path) -> None:
    if not script_path.exists():
        raise FileNotFoundError(f"Missing ingestion script: {script_path.name}")

    LOGGER.info("Starting script: %s", script_path.name)
    result = subprocess.run([sys.executable, str(script_path)], cwd=str(SCRIPT_DIR), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Ingestion script failed: {script_path.name}")
    LOGGER.info("Completed script: %s", script_path.name)


def run_all() -> int:
    configure_logging()
    try:
        for script_path in SCRIPTS:
            run_script(script_path)
        LOGGER.info("All ingestion scripts completed successfully.")
        return 0
    except Exception as error:
        LOGGER.error("Master ingestion stopped securely. error_type=%s", error.__class__.__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_all())
