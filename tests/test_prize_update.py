import pytest

from tools.invoice_center.prize_update import _is_missing_archive, _validate_period


@pytest.mark.parametrize("value", ["20260102", "20260304", "20260506", "20260708", "20260910", "20261112"])
def test_validate_period_accepts_bimonthly_periods(value):
    assert _validate_period(value) == value


@pytest.mark.parametrize("value", ["", "202607", "20260709", "20260203", "abcdefgh"])
def test_validate_period_rejects_invalid_periods(value):
    with pytest.raises(ValueError):
        _validate_period(value)


def test_missing_archive_detection_is_exact_for_area_and_period():
    exc = RuntimeError("Google Drive 找不到檔案：20260708中獎發票-台北.zip")
    assert _is_missing_archive(exc, "台北", "20260708")
    assert not _is_missing_archive(exc, "台中", "20260708")
    assert not _is_missing_archive(exc, "台北", "20260506")
