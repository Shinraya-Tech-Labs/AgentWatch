"""PHI patterns of the HIPAA compliance mode (governance/hipaa.py)."""

from __future__ import annotations

import pytest

from panoptis.governance.hipaa import redact_phi


@pytest.mark.parametrize(
    ("text", "labels", "gone"),
    [
        ("SSN 123-45-6789 on file", ["ssn"], "123-45-6789"),
        ("ssn: 123456789", ["ssn"], "123456789"),
        ("email patient.jones@example.com", ["email"], "jones@example.com"),
        ("Patient: AB12345 seen today", ["mrn"], "AB12345"),
        ("patient id 998877", ["mrn"], "998877"),
        ("MRN: 1234567 diagnosed with diabetes", ["mrn", "condition"], "1234567"),
    ],
)
def test_phi_is_detected_and_removed(text: str, labels: list[str], gone: str) -> None:
    r = redact_phi(text)
    assert [f.label for f in r.findings] == labels
    assert gone not in r.redacted


@pytest.mark.parametrize(
    "text",
    [
        "the patient improved steadily",  # the word alone is not a record number
        "order 000-12-3456 ref",  # area 000 is never issued: not an SSN
    ],
)
def test_ordinary_text_is_not_phi(text: str) -> None:
    r = redact_phi(text)
    assert r.findings == [] and r.redacted == text


def test_email_local_part_is_not_labelled_a_record_number() -> None:
    """Regression: 'patients-portal@clinic.org' was redacted as an MRN ('s-portal')."""
    r = redact_phi("contact patients-portal@clinic.org")
    assert [f.label for f in r.findings] == ["email"]
