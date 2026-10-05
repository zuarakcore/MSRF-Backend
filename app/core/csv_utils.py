"""CSV export helpers."""

import csv
import io
from collections.abc import Iterable, Mapping
from datetime import date

from fastapi.responses import StreamingResponse

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> str:
    """Neutralise spreadsheet formula injection (a name like "=HYPERLINK(...)" must not execute)."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def csv_response(filename: str, header: list[str], rows: Iterable[Mapping[str, object]]) -> StreamingResponse:
    def generate() -> Iterable[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        yield "﻿" + buffer.getvalue()  # BOM: Excel opens UTF-8 (Malayalam names) correctly
        for row in rows:
            buffer.seek(0)
            buffer.truncate()
            writer.writerow([safe_cell(row.get(col)) for col in header])
            yield buffer.getvalue()

    stamped = f"{filename}_{date.today().isoformat()}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{stamped}"'},
    )
