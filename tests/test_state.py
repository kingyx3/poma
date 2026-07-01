from poma.state import LocalState


def test_session_attempt_prevents_duplicate_runs(tmp_path) -> None:
    state = LocalState(tmp_path)
    assert not state.has_session_attempt("2026-06-22")

    state.begin_session("2026-06-22", "run-1")

    assert state.has_session_attempt("2026-06-22")
    assert state.session_status("2026-06-22") == "running"


def test_failed_session_is_terminal_for_automatic_retry(tmp_path) -> None:
    state = LocalState(tmp_path)
    state.begin_session("2026-06-22", "run-1")
    state.mark_session("2026-06-22", "run-1", "failed", error="broker disconnected")

    assert state.has_session_attempt("2026-06-22")
    assert state.session_status("2026-06-22") == "failed"


def test_zero_accepted_orders_is_terminal_for_automatic_retry(tmp_path) -> None:
    state = LocalState(tmp_path)
    state.mark_session("2026-06-29", "run-1", "no_orders_accepted")

    assert state.has_session_attempt("2026-06-29")
    assert state.session_status("2026-06-29") == "no_orders_accepted"


def test_unknown_session_status_is_not_terminal(tmp_path) -> None:
    state = LocalState(tmp_path)
    state.mark_session("2026-06-29", "run-1", "unexpected")

    assert not state.has_session_attempt("2026-06-29")
    assert state.session_status("2026-06-29") == "unexpected"


def test_session_run_id_returns_the_run_id_for_the_matching_session(tmp_path) -> None:
    state = LocalState(tmp_path)
    state.begin_session("2026-06-22", "run-1")

    assert state.session_run_id("2026-06-22") == "run-1"
    assert state.session_run_id("2026-06-21") is None
