"""UJS docket parser: both report families, absent fields, and trim discipline.

The page text below reproduces the column geometry of the real reports (space
aligned, two-or-more-space cell separators). Every fixture was cut down from a
retrieved docket, so the parser is exercised against the layout it will meet in
production rather than against a simplification of it.
"""

from __future__ import annotations

import pytest

from pa_docket.domain.enums import PresenceStatus, ValidationStatus
from pa_docket.parsers import NonCriminalDocketError
from pa_docket.parsers.ujs import UJSDocketParser, docket_kind, normalize_disposition

MDJ_PAGE_1 = """                         Magisterial District Judge 51-3-02

                                   DOCKET
                                                             Docket Number: MJ-51302-CR-0000239-2026
                                                               Criminal Docket

                                          Commonwealth of Pennsylvania
                                                                                v.
                                                  Cortez, Juan Carlos
                                                                                                              Page 1 of 2

                                       CASE INFORMATION
 Judge Assigned:           Tyler, Mark R.                                Issue Date:              10/19/2026
 OTN:             Q 1012917-3                                       File Date:               10/19/2026
 Arresting Agency:         Adams County Sheriff's Office             Arrest Date:
 Complaint No.:           2500100861                                     Incident No.:            25-0010086
 Disposition:                  Guilty Plea                                      Disposition Date:         01/10/2027
 County:             Adams
 Case Status:                Active

                                       STATUS INFORMATION
 Case Status                  Status Date         Processing Status
 Active                      10/19/2026          Awaiting Preliminary Hearing

                                       DEFENDANT INFORMATION
 Date Of Birth:               01/01/1990             City/State/Zip: Gettysburg, PA 17325
 Race:                Black                           Sex:             Male

                                       CASE PARTICIPANTS
 Participant Type            Participant Name
 Defendant                   Cortez, Juan Carlos
 Arresting Officer           Adams, Tyler J.

                                             CHARGES

        # Charge                  Grade  Description                                           Offense Dt.   Disposition
        1 75 \u00a7 3802 \u00a7\u00a7 D1i*      M     DRIVING UNDER INFLUENCE OF                     10/19/2026   Guilty Plea
                                  ALCOHOL/CONTROLLED SUB
        2 35 \u00a7 780-113 \u00a7\u00a7 A32     M    USE/POSS OF DRUG PARAPH                        10/19/2026
        3 75 \u00a7 1301 \u00a7\u00a7 A          S    REG REQUIRED - PA REG EXP < 60 DAYS (NOT        10/19/2026
                                FOR MOTOR CARRIER)
"""

MDJ_PAGE_2 = """
MDJS 1200                                                      Page 2 of 2                                                      Printed: 11/06/2026 11:44 am

        Recent entries made in the court filing offices may not be immediately reflected on these docket sheets . Neither the courts of the
       Commonwealth of Pennsylvania nor the Administrative Office of Pennsylvania Courts assumes any liability for inaccurate or delayed data
"""

CP_PAGE_1 = """          COURT OF COMMON PLEAS OF YORK COUNTY
                                  DOCKET

                                                          Docket Number: CP-67-CR-0005304-2026
                                               CRIMINAL DOCKET
                                                                                               Court Case

                                        Commonwealth of Pennsylvania
                                                                                                          Page 1 of 2
                                                                             v.
                                                          Jaiwilliam Tyquan Sease
                                     CASE INFORMATION

 Judge Assigned: Vedder, Clyde W.                               Date Filed: 11/05/2026              Initiation Date: 10/13/2026
 Arresting Agency: York County Detectives                              Arresting Officer: Gartrell, Patrick G. Jr.
 County:           York
 Case Status:    Active                   Status Date         Processing Status

                                       CASE PARTICIPANTS
 Participant Type                      Name
 Defendant                                Sease, Jaiwilliam Tyquan

                                       CHARGES
   Seq.       Orig Seq.   Grade    Statute                   Statute Description                            Offense Dt.   OTN
   1        1        F       35 \u00a7 780-113 \u00a7\u00a7 A30      Manufacture, Delivery, or Possession With       10/13/2026    A 1011079-6
                                                                     Intent to Manufacture or Deliver
   2        2        M       35 \u00a7 780-113 \u00a7\u00a7 A32     Use/Poss Of Drug Paraph                         10/13/2026    A 1011079-6
"""
CP_PAGE_2 = """
CPCMS 9082                                                                                                                        Printed: 11/06/2026

           Recent entries made in the court filing offices may not be immediately reflected on these docket sheets . Neither the court
          System of the Commonwealth of Pennsylvania nor the Administrative Office of Pennsylvania Courts assume any liability for
       COURT OF COMMON PLEAS OF YORK COUNTY
                                  DOCKET
                                                          Docket Number: CP-67-CR-0005304-2026
                                               CRIMINAL DOCKET
                                                                                               Court Case
                                                                             v.
                                                          Jaiwilliam Tyquan Sease
                                     DISPOSITION SENTENCING/PENALTIES
Disposition
  Case Event                                                      Disposition Date                         Final Disposition
    Sequence/Description                                       Offense Disposition                  Grade    Section

Held for Court (Lower Court)          Defendant Was Not Present
  Lower Court Disposition                                     11/02/2026                         Not Final
    1 / Manufacture, Delivery, or Possession With Intent to         Held for Court (Lower Court)          F        35 \u00a7 780-113 \u00a7\u00a7 A30
    Manufacture or Deliver
    2 / Use/Poss Of Drug Paraph                                     Held for Court (Lower Court)          M        35 \u00a7 780-113 \u00a7\u00a7 A32
 COMMONWEALTH INFORMATION                    ATTORNEY INFORMATION
"""

LT_DOCKET = """                         Magisterial District Judge 19-1-01

                                   DOCKET
                                                              Docket Number: MJ-19101-LT-0000421-2026
                                                      Landlord/Tenant Docket

                                             Red Door Property
                                                                                 v.
                                                     Cheryl  Cirilo, Ron Capers
                                                                                                              Page 1 of 1

                                       CASE INFORMATION
 Judge Assigned:         Thomas L. Harteis                                  File Date:               11/05/2026
 County:                 York

                                             DISPOSITION SUMMARY
"""


def parse(*pages: str):
    return UJSDocketParser().parse_pages(list(pages))


# --------------------------------------------------------------------- MDJ


def test_mdj_extracts_every_requested_field():
    record = parse(MDJ_PAGE_1, MDJ_PAGE_2)
    assert record.docket_number.normalized_value == "MJ-51302-CR-0000239-2026"
    assert record.filing_date.normalized_value == "2026-10-19"
    assert record.county.normalized_value == "Adams"
    assert record.defendants[0].name.normalized_value == "Cortez, Juan Carlos"
    assert record.defendants[0].race.raw_value == "Black"
    assert record.defendants[0].race.presence_status == PresenceStatus.PRESENT
    assert record.case_status.raw_value == "Active"
    assert record.metadata["case_disposition"]["raw_value"] == "Guilty Plea"
    assert record.metadata["case_disposition"]["normalized_value"] == "guilty"
    assert record.metadata["case_disposition_date"] == "2027-01-10"
    assert record.metadata["document_family"] == "MDJ"
    assert len(record.charges) == 3

    first = record.charges[0]
    assert first.charge_number.normalized_value == "1"
    assert first.description.normalized_value == (
        "DRIVING UNDER INFLUENCE OF ALCOHOL/CONTROLLED SUB"
    )
    assert first.offense_date.normalized_value == "2026-10-19"
    assert first.arresting_officer.normalized_value == "Adams, Tyler J."
    assert first.disposition.normalized_value == "guilty"
    assert first.disposition_date.normalized_value == "2027-01-10"


def test_mdj_wrapped_description_never_absorbs_page_furniture():
    record = parse(MDJ_PAGE_1, MDJ_PAGE_2)
    for charge in record.charges:
        text = (charge.description.raw_value or "").casefold()
        assert "recent entries" not in text
        assert "mdjs 1200" not in text
        assert "printed:" not in text
    assert record.charges[2].description.normalized_value == (
        "REG REQUIRED - PA REG EXP < 60 DAYS (NOT FOR MOTOR CARRIER)"
    )


def test_mdj_absent_race_and_officer_are_not_recorded_not_invented():
    page = MDJ_PAGE_1.replace(
        " Race:                Black                           Sex:             Male\n", ""
    ).replace(" Arresting Officer           Adams, Tyler J.\n", "")
    record = parse(page, MDJ_PAGE_2)
    assert record.defendants[0].race.raw_value is None
    assert record.defendants[0].race.presence_status == PresenceStatus.NOT_RECORDED
    assert record.charges[0].arresting_officer.raw_value is None
    assert record.charges[0].arresting_officer.presence_status == PresenceStatus.NOT_RECORDED


def test_mdj_unrecognised_disposition_is_kept_raw_and_flagged():
    page = MDJ_PAGE_1.replace("10/19/2026   Guilty Plea", "10/19/2026   Deferred Adjudication")
    record = parse(page, MDJ_PAGE_2)
    first = record.charges[0]
    assert first.disposition.raw_value == "Deferred Adjudication"
    assert first.disposition.normalized_value is None
    assert first.disposition.presence_status == PresenceStatus.UNVERIFIED
    assert any("unrecognised disposition" in error for error in record.validation_errors)


def test_mdj_criminal_docket_with_no_charge_rows_is_needs_review():
    page = MDJ_PAGE_1.split("                                             CHARGES")[0]
    record = parse(page, MDJ_PAGE_2)
    assert record.charges == []
    assert record.validation_status == ValidationStatus.NEEDS_REVIEW
    assert any("No charge rows found" in error for error in record.validation_errors)


# ---------------------------------------------------------------------- CP


def test_cp_extracts_officer_and_per_charge_disposition_by_sequence():
    record = parse(CP_PAGE_1, CP_PAGE_2)
    assert record.docket_number.normalized_value == "CP-67-CR-0005304-2026"
    assert record.county.normalized_value == "York"
    assert record.metadata["document_family"] == "CP"
    assert record.charges[0].arresting_officer.normalized_value == "Gartrell, Patrick G. Jr."
    assert record.charges[0].description.normalized_value == (
        "Manufacture, Delivery, or Possession With Intent to Manufacture or Deliver"
    )
    assert [charge.disposition.normalized_value for charge in record.charges] == ["held", "held"]
    assert [charge.disposition_date.normalized_value for charge in record.charges] == [
        "2026-11-02",
        "2026-11-02",
    ]


def test_cp_case_status_and_case_disposition_are_separate_facts():
    record = parse(CP_PAGE_1, CP_PAGE_2)
    assert record.case_status.raw_value == "Active"
    assert record.case_status.normalized_value == "Active"
    disposition = record.metadata["case_disposition"]
    assert disposition["raw_value"] == "Held for Court (Lower Court)"
    assert disposition["normalized_value"] == "held"
    assert record.metadata["case_final_disposition"] == "Not Final"


def test_cp_without_a_race_field_still_validates():
    """Race is absent from the CPCMS form, so it never demotes a complete read."""
    record = parse(CP_PAGE_1, CP_PAGE_2)
    assert record.defendants[0].race.presence_status == PresenceStatus.NOT_RECORDED
    assert record.validation_status == ValidationStatus.VALIDATED
    assert record.validation_errors == []


def test_cp_generic_event_label_defers_to_the_final_disposition():
    page = CP_PAGE_2.replace(
        "Held for Court (Lower Court)          Defendant Was Not Present",
        "Lower Court Proceeding (generic)      Defendant Was Not Present",
    )
    record = parse(CP_PAGE_1, page)
    disposition = record.metadata["case_disposition"]
    assert disposition["raw_value"] == "Not Final"
    assert disposition["normalized_value"] == "pending"
    assert record.metadata["case_event_label"] == "Lower Court Proceeding (generic)"


def test_cp_has_no_race_field_and_says_so():
    record = parse(CP_PAGE_1, CP_PAGE_2)
    race = record.defendants[0].race
    assert race.raw_value is None
    assert race.normalized_value is None
    assert race.presence_status == PresenceStatus.NOT_RECORDED
    assert race.evidence_reference == "p1:DEFENDANT INFORMATION"


def test_cp_page_banner_and_caption_never_fold_into_a_charge():
    record = parse(CP_PAGE_1, CP_PAGE_2)
    for charge in record.charges:
        text = (charge.description.raw_value or "").casefold()
        assert "court case" not in text
        assert "sease" not in text
        assert "cpcms" not in text
    assert record.charges[1].description.normalized_value == "Use/Poss Of Drug Paraph"


def test_cp_charge_without_a_disposition_stays_missing():
    extra = (
        "\n   3        3        S       18 \u00a7 2709 \u00a7\u00a7 A1        Harassment"
        "                                     10/13/2026    A 1011079-6"
    )
    record = parse(CP_PAGE_1 + extra, CP_PAGE_2)
    third = record.charges[2]
    assert third.disposition.raw_value is None
    assert third.disposition.presence_status == PresenceStatus.MISSING


# ------------------------------------------------------------------ families


def test_non_criminal_docket_is_named_not_failed():
    with pytest.raises(NonCriminalDocketError) as error:
        parse(LT_DOCKET)
    assert "Landlord/Tenant" in str(error.value)
    assert "MJ-19101-LT-0000421-2026" in str(error.value)


def test_docket_kind_reads_the_declared_type_not_the_number():
    assert docket_kind(CP_PAGE_1) == "Criminal"
    assert docket_kind(LT_DOCKET) == "Landlord/Tenant"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Guilty Plea", "guilty"),
        ("HP - Held for Court", "held"),
        ("Not Guilty", "not-guilty"),
        ("Nolle Prosequi", "dismissed"),
        ("Charge Changed", "changed"),
        ("Active", "pending"),
        ("Not Final", "pending"),
        ("Something Else", None),
    ],
)
def test_disposition_normalisation(raw, expected):
    assert normalize_disposition(raw) == expected
