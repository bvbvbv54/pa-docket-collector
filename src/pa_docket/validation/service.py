from pa_docket.domain.enums import PresenceStatus, ValidationStatus
from pa_docket.domain.models import DocketRecord


def validate_record(record: DocketRecord) -> DocketRecord:
    errors = list(record.validation_errors)
    numbers = [c.charge_number.normalized_value for c in record.charges]
    if len(numbers) != len(set(numbers)):
        errors.append("Duplicate charge numbers")
    defendant_ids = {d.index for d in record.defendants}
    if any(c.defendant_index not in defendant_ids for c in record.charges):
        errors.append("A charge references a defendant index that was not extracted")
    for defendant in record.defendants:
        if defendant.race.presence_status != PresenceStatus.PRESENT and defendant.race.raw_value is not None:
            errors.append("Race field has a value but is not marked PRESENT")
    if errors and record.validation_status == ValidationStatus.VALIDATED:
        record.validation_status = ValidationStatus.NEEDS_REVIEW
    record.validation_errors = errors
    return record
