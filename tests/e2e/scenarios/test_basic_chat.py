"""Scenario B — Basic chat flows."""

from __future__ import annotations

import json

from tests.e2e.harness import (
    E2EConfig, assert_exit_ok, assert_output, base_env, find_history_files, run_anton,
)


def test_single_turn_response_visible(cfg, stub, tmp_path):
    stub.queue_text("UNIQUE_RESPONSE_MARKER_XYZ")
    result = run_anton(["--folder", str(tmp_path)], ["hello world", "exit"],
                       env=base_env(stub), timeout=cfg.timeout(20))
    assert_exit_ok(result)
    if cfg.live:
        assert len((result.stdout + result.stderr).strip()) > 0
    else:
        assert_output(result, "UNIQUE_RESPONSE_MARKER_XYZ")


def test_multi_turn_responses_visible(cfg, stub, tmp_path):
    stub.queue_text("Hi there! TURN_ONE_REPLY")
    stub.queue_text("Sure! TURN_TWO_REPLY")
    result = run_anton(["--folder", str(tmp_path)],
                       ["first message", "second message", "exit"],
                       env=base_env(stub), timeout=cfg.timeout(25))
    assert_exit_ok(result)
    if cfg.live:
        assert len((result.stdout + result.stderr).strip()) > 0
    else:
        assert_output(result, "TURN_ONE_REPLY")
        assert_output(result, "TURN_TWO_REPLY")


def test_blank_input_no_crash(cfg, stub, tmp_path):
    stub.queue_text("Fine reply after blank.")
    result = run_anton(["--folder", str(tmp_path)], ["", "hello", "exit"],
                       env=base_env(stub), timeout=cfg.timeout(20))
    assert not result.timed_out, f"App hung on blank input\n{result}"


def test_exit_keyword_terminates(cfg, stub, tmp_path):
    stub.queue_text("Response before exit.")
    result = run_anton(["--folder", str(tmp_path)], ["say something", "exit"],
                       env=base_env(stub), timeout=cfg.timeout(20))
    assert_exit_ok(result)
    assert not result.timed_out


def test_history_file_created_with_correct_content(cfg, stub, tmp_path):
    stub.queue_text("History test response 42.")
    result = run_anton(["--folder", str(tmp_path)], ["history test message", "exit"],
                       env=base_env(stub, memory_enabled=True), timeout=cfg.timeout(20))
    assert_exit_ok(result)

    history_files = find_history_files(tmp_path)
    assert len(history_files) >= 1, "No history files found in workspace"

    history = json.loads(history_files[0].read_text())
    roles = [m["role"] for m in history]
    assert "user" in roles
    assert "assistant" in roles

    user_contents = [m["content"] for m in history if m["role"] == "user"]
    assert any("history test message" in str(c) for c in user_contents), \
        f"User message not in history: {user_contents}"

    if not cfg.live:
        asst_contents = [m["content"] for m in history if m["role"] == "assistant"]
        assert any("History test response 42" in str(c) for c in asst_contents), \
            f"Assistant response not in history: {asst_contents}"
