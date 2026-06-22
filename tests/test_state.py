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
    assert state.has_rebalanced("2026-06-22")
    assert state.session_status("2026-06-22") == "failed"
