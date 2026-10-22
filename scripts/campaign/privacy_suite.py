"""Private data routes; keep genuine product contract failures visible."""

from critical_suite import BASE, RUN, EXECUTORS, execute, require_green


def test_private_data_contracts():
    result = execute(
        BASE,
        "privacy-baseline",
        [
            str(EXECUTORS / "extended_cases.py") + "::" + name
            for name in (
                "test_SEC02_SEC08_export_and_favorites_owner_isolation",
                "test_SEC08_export_owned_missing_report_contract",
                "test_SEC02_SEC04_clue_location_and_media_permissions",
            )
        ],
    )
    require_green(result)


def test_export_contract_on_official_snapshot():
    # Separate process/schema; no checkout or main branch mutation.
    result = execute(
        RUN / "sources/A/api",
        "official-export",
        [
            str(EXECUTORS / "extended_cases.py") + "::" + name
            for name in (
                "test_SEC02_SEC08_export_and_favorites_owner_isolation",
                "test_SEC08_export_owned_missing_report_contract",
            )
        ],
    )
    require_green(result)
