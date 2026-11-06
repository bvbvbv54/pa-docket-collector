from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pa_docket.domain.enums import PresenceStatus, ValidationStatus


class FieldValue(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    raw_value: str | None = None
    normalized_value: str | None = None
    presence_status: PresenceStatus
    evidence_reference: str | None = None


class Defendant(BaseModel):
    index: int
    name: FieldValue
    race: FieldValue


class Charge(BaseModel):
    charge_number: FieldValue
    defendant_index: int = 1
    description: FieldValue
    statute: FieldValue
    grade: FieldValue
    offense_date: FieldValue
    arresting_officer: FieldValue
    disposition: FieldValue
    disposition_date: FieldValue


class DocketRecord(BaseModel):
    docket_number: FieldValue
    court: FieldValue
    county: FieldValue
    filing_date: FieldValue
    case_status: FieldValue
    related_docket_numbers: list[FieldValue] = Field(default_factory=list)
    defendants: list[Defendant] = Field(default_factory=list)
    charges: list[Charge] = Field(default_factory=list)
    parser_version: str
    validation_status: ValidationStatus
    validation_errors: list[str] = Field(default_factory=list)
    source_pdf_url: str | None = None
    retrieved_at: datetime | None = None
    source_document_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def filing_date_value(self) -> date | None:
        value = self.filing_date.normalized_value
        return date.fromisoformat(value) if value else None
