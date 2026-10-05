"""CSV import (all-or-nothing) and template for students."""

import csv
import io
from datetime import date
from typing import Any

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Batch, RecordStatus, Relationship
from app.core.errors import BusinessRuleViolation
from app.modules.files.service import read_limited
from app.modules.reference.models import Category, ProgramType, TrainingCenter
from app.modules.students import service
from app.modules.students.models import Student
from app.modules.students.schemas import StudentIn

MAX_ROWS = 1000
MAX_BYTES = 2 * 1024 * 1024

# CSV header -> StudentIn field
COLUMNS: dict[str, str] = {
    "Full Name": "full_name",
    "Gender": "gender",
    "Blood Group": "blood_group",
    "Date of Birth": "date_of_birth",
    "Phone": "phone",
    "Email": "email",
    "Address": "address",
    "Admission Number": "admission_number",
    "Admission Date": "admission_date",
    "Category": "category_id",
    "Program Type": "program_type_id",
    "Training Center": "training_center_id",
    "Batch": "batch",
    "Monthly Fee": "monthly_fee",
    "Parent Name": "parent_name",
    "Parent Relationship": "parent_relationship",
    "Parent Phone": "parent_phone",
    "Parent Email": "parent_email",
    "Parent Address": "parent_address",
    "Emergency Name": "emergency_name",
    "Emergency Relationship": "emergency_relationship",
    "Emergency Phone": "emergency_phone",
}
FIELD_TO_COLUMN = {v: k for k, v in COLUMNS.items()}
REQUIRED_COLUMNS = [
    "Full Name",
    "Gender",
    "Date of Birth",
    "Category",
    "Program Type",
    "Training Center",
    "Parent Name",
    "Parent Phone",
]


class CsvInvalid(BusinessRuleViolation):
    default_code = "CSV_INVALID"
    default_detail = "The CSV file has errors. Nothing was imported."


def template_rows() -> list[dict[str, object]]:
    return [
        {
            "Full Name": "Arjun K",
            "Gender": "MALE",
            "Blood Group": "O+",
            "Date of Birth": "2012-05-14",
            "Phone": "9847112233",
            "Email": "arjun@example.com",
            "Address": "Calicut, Kerala",
            "Admission Number": "",
            "Admission Date": date.today().isoformat(),
            "Category": "Youth Football Squad (U-13)",
            "Program Type": "Day Scholar Program",
            "Training Center": "Kozhikode Main Campus",
            "Batch": "MORNING",
            "Monthly Fee": "2000",
            "Parent Name": "Krishnan K",
            "Parent Relationship": "FATHER",
            "Parent Phone": "9447112233",
            "Parent Email": "krishnan@example.com",
            "Parent Address": "",
            "Emergency Name": "Krishnan K",
            "Emergency Relationship": "Father",
            "Emergency Phone": "9447112233",
        }
    ]


async def _lookup(
    db: AsyncSession, model: type[Category] | type[ProgramType] | type[TrainingCenter]
) -> dict[str, Any]:
    rows = await db.scalars(select(model).where(model.status == RecordStatus.ACTIVE))
    return {r.name.strip().lower(): r.id for r in rows}


def _normalise_batch(value: str) -> str:
    """Accept the enum value or the UI label ("Morning (6:00 AM - 8:00 AM)")."""
    v = (value or Batch.MORNING).strip().upper()
    return next((b.value for b in Batch if v.startswith(b.value)), v)


async def import_students(db: AsyncSession, upload: UploadFile, *, dry_run: bool) -> int:
    raw = await read_limited(upload, MAX_BYTES)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvInvalid("The file must be UTF-8 encoded CSV") from exc

    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise CsvInvalid(f"Missing required columns: {', '.join(missing)}")

    lookups = {
        "category_id": await _lookup(db, Category),
        "program_type_id": await _lookup(db, ProgramType),
        "training_center_id": await _lookup(db, TrainingCenter),
    }
    existing_numbers = {n.lower() for n in await db.scalars(select(func.lower(Student.admission_number)))}

    errors: list[dict[str, Any]] = []
    valid: list[StudentIn] = []
    seen_numbers: set[str] = set()
    for index, row in enumerate(reader, start=2):  # row 1 is the header
        if index - 1 > MAX_ROWS:
            raise CsvInvalid(f"At most {MAX_ROWS} rows can be imported at once")
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        if not any(row.values()):
            continue
        data: dict[str, Any] = {}
        row_errors: list[dict[str, Any]] = []
        for column, field in COLUMNS.items():
            value = row.get(column, "")
            if field in lookups:
                ref = lookups[field].get(value.lower())
                if ref is None:
                    row_errors.append(
                        {"row": index, "field": column, "message": f"Unknown or inactive: '{value}'"}
                    )
                data[field] = ref
            elif field == "batch":
                data[field] = _normalise_batch(value)
            elif field == "parent_relationship":
                data[field] = (value or Relationship.FATHER).upper()
            elif field == "gender":
                data[field] = value.upper()
            elif field == "admission_date":
                data[field] = value or date.today().isoformat()
            elif field == "monthly_fee":
                data[field] = value or "0"
            else:
                data[field] = value or None

        number = (data.get("admission_number") or "").lower()
        if number and (number in existing_numbers or number in seen_numbers):
            row_errors.append({"row": index, "field": "Admission Number", "message": "Already in use"})
        seen_numbers.add(number)

        if not row_errors:
            try:
                valid.append(StudentIn.model_validate(data))
            except ValidationError as exc:
                for err in exc.errors():
                    field = str(err["loc"][0]) if err["loc"] else ""
                    row_errors.append(
                        {"row": index, "field": FIELD_TO_COLUMN.get(field, field), "message": err["msg"]}
                    )
        errors.extend(row_errors)

    if errors:
        raise CsvInvalid(errors=errors[:200])
    if not valid:
        raise CsvInvalid("The file contains no student rows")
    if dry_run:
        return len(valid)
    for item in valid:
        await service.build_student(db, item)
    await db.commit()
    return len(valid)
