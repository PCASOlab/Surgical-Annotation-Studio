"""
io_utils/excel_copies.py

Brings every Excel copy (see io_utils/xlsx_mirror.py) up to date with its
CSV -- for copies that couldn't be written while open in Excel, or that
don't exist yet (projects from before the copies were added).
"""
from __future__ import annotations

from io_utils import clinical_io, dlc_io, xlsx_mirror


def refresh_stale(pm) -> list[str]:
    """Rebuild missing/outdated copies; returns the names rebuilt. A CSV
    that can't be read right now is simply skipped (tried again next time)."""
    rebuilt = []
    jobs = [(p, clinical_io.write_score_mirror)
            for p in clinical_io.all_score_entry_files(pm.paths.clinical)
            if clinical_io.RATER_TAG in p.name]
    if pm.paths.labeled_data.is_dir():
        jobs += [(p, dlc_io.write_collected_data_mirror)
                 for p in sorted(pm.paths.labeled_data.glob("CollectedData_*.csv"))]
    for csv_path, write in jobs:
        try:
            if xlsx_mirror.is_stale(csv_path) and write(csv_path):
                rebuilt.append(xlsx_mirror.mirror_path(csv_path).name)
        except OSError:
            continue
    return rebuilt
