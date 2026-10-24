import time
import pytest
from core.services.submission_lock import (
    submission_lock,
    is_submission_active,
    SubmissionLockError,
    CrossProcessLock,
    _get_lockfile_path,
)


@pytest.mark.django_db
def test_submission_lock_acquisition_and_release():
    """Verify that lock acquires, records metadata, and releases cleanly."""
    active, _ = is_submission_active()
    assert not active

    with submission_lock(pair_id=101, order_number="PO-TEST-1", registration_number="UMA101A", operator="test_op"):
        active, info = is_submission_active()
        assert active
        assert info.get("pair_id") == 101
        assert info.get("registration_number") == "UMA101A"
        assert info.get("operator") == "test_op"

    active, _ = is_submission_active()
    assert not active


@pytest.mark.django_db
def test_submission_lock_mutual_exclusion():
    """Verify that concurrent acquisition raises SubmissionLockError when non-blocking."""
    with submission_lock(pair_id=202, order_number="PO-TEST-2", registration_number="UMA202B", operator="user_a"):
        with pytest.raises(SubmissionLockError) as exc_info:
            with submission_lock(pair_id=303, order_number="PO-TEST-3", registration_number="UMA303C", operator="user_b", blocking=False):
                pass

        err_msg = str(exc_info.value)
        assert "Another submission is currently in progress" in err_msg
        assert "UMA202B" in err_msg
        assert "user_a" in err_msg


@pytest.mark.django_db
def test_submission_lock_blocking_timeout():
    """Verify that blocking acquisition times out if lock remains held."""
    with submission_lock(pair_id=404, order_number="PO-TEST-4", registration_number="UMA404D", operator="holder"):
        start = time.time()
        with pytest.raises(SubmissionLockError):
            with submission_lock(pair_id=505, blocking=True, timeout=0.6):
                pass
        elapsed = time.time() - start
        assert elapsed >= 0.5
