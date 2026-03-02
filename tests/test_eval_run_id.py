"""Tests for _extract_eval_run_id (M6 Phase 1)."""

from __future__ import annotations

from src.gateway.dispatch import _extract_eval_run_id


class TestExtractEvalRunId:
    def test_online_session_returns_empty(self) -> None:
        assert _extract_eval_run_id("main") == ""

    def test_non_eval_prefix_returns_empty(self) -> None:
        assert _extract_eval_run_id("some_random_session") == ""

    def test_gemini_eval_session(self) -> None:
        result = _extract_eval_run_id("m6_eval_gemini_T10_1740000000")
        assert result == "m6_eval_gemini_1740000000"

    def test_openai_eval_session(self) -> None:
        result = _extract_eval_run_id("m6_eval_openai_T12_1740000000")
        assert result == "m6_eval_openai_1740000000"

    def test_task_id_with_underscore(self) -> None:
        """Task id containing underscore: timestamp is always last segment."""
        result = _extract_eval_run_id("m6_eval_gemini_T10_retry_1740000000")
        assert result == "m6_eval_gemini_1740000000"

    def test_same_run_different_tasks_same_run_id(self) -> None:
        """Same eval run (same provider + timestamp), different tasks → same eval_run_id."""
        id1 = _extract_eval_run_id("m6_eval_gemini_T10_1740000000")
        id2 = _extract_eval_run_id("m6_eval_gemini_T11_1740000000")
        assert id1 == id2

    def test_short_eval_session_fallback(self) -> None:
        """Less than 5 parts → fallback to full session_id."""
        result = _extract_eval_run_id("m6_eval_gemini_T10")
        assert result == "m6_eval_gemini_T10"

    def test_empty_string(self) -> None:
        assert _extract_eval_run_id("") == ""
