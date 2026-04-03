"""Scenario E — Session persistence."""

from __future__ import annotations

import json
import time

from scripts.e2e.harness import (
    E2EConfig, assert_exit_ok, assert_output, base_env, find_history_files, run_anton,
)


def test_new_session_creates_history_file(cfg, stub, tmp_path):
    stub.queue_text("Hello! SESSION_CREATED")
    stub.queue_verification_ok()
    result = run_anton(["--folder", str(tmp_path)], ["hi there", "exit"],
                       env=base_env(stub, memory_enabled=True), timeout=cfg.timeout(20))
    assert_exit_ok(result)
    assert len(find_history_files(tmp_path)) >= 1, \
        f"No history files created. stdout: {result.stdout[:300]}"


def test_sessions_subcommand_lists_session(cfg, stub, tmp_path):
    stub.queue_text("Session reply.")
    stub.queue_verification_ok()
    run_anton(["--folder", str(tmp_path)], ["create a session", "exit"],
              env=base_env(stub, memory_enabled=True), timeout=cfg.timeout(20))

    result = run_anton(["--folder", str(tmp_path), "sessions"], [],
                       env=base_env(stub, memory_enabled=True), timeout=cfg.timeout(10))
    assert_exit_ok(result)
    assert len((result.stdout + result.stderr).strip()) > 0, "Sessions command produced no output"


def test_history_file_contains_correct_messages(cfg, stub, tmp_path):
    marker = "HISTORY_MARKER_E3"
    stub.queue_text(f"The answer is: {marker}")
    stub.queue_verification_ok()
    result = run_anton(["--folder", str(tmp_path)], ["what is the marker", "exit"],
                       env=base_env(stub, memory_enabled=True), timeout=cfg.timeout(20))
    assert_exit_ok(result)

    history_files = find_history_files(tmp_path)
    assert len(history_files) >= 1, "No history files found"
    history = json.loads(history_files[0].read_text())

    roles = [m["role"] for m in history]
    assert "user" in roles
    assert "assistant" in roles
    assert "what is the marker" in json.dumps(history), "User message not in history"
    if not cfg.live:
        assert marker in json.dumps(history), f"Assistant marker not in history"


def test_two_runs_same_folder_no_corruption(cfg, tmp_path):
    with cfg.make_provider() as stub1:
        stub1.queue_text("First reply.")
        stub1.queue_verification_ok()
        r1 = run_anton(["--folder", str(tmp_path)], ["first message", "exit"],
                       env=base_env(stub1), timeout=cfg.timeout(20))
    assert_exit_ok(r1)
    if not cfg.live:
        assert_output(r1, "First reply.")

    with cfg.make_provider() as stub2:
        stub2.queue_text("Second reply. SECOND_OK")
        stub2.queue_verification_ok()
        r2 = run_anton(["--folder", str(tmp_path)], ["second message", "exit"],
                       env=base_env(stub2), timeout=cfg.timeout(20))
    assert_exit_ok(r2)
    if not cfg.live:
        assert_output(r2, "SECOND_OK")


def test_two_runs_create_distinct_session_files(cfg, stub, tmp_path):
    stub.queue_text("Session A reply.")
    stub.queue_verification_ok()
    stub.queue_text("Session B reply.")
    stub.queue_verification_ok()
    env = base_env(stub, memory_enabled=True)

    run_anton(["--folder", str(tmp_path)], ["session A", "exit"],
              env=env, timeout=cfg.timeout(20))
    time.sleep(1.1)
    run_anton(["--folder", str(tmp_path)], ["session B", "exit"],
              env=env, timeout=cfg.timeout(20))

    history_files = find_history_files(tmp_path)
    assert len(history_files) >= 2, \
        f"Expected >=2 history files, got {len(history_files)}"
    names = [f.name for f in history_files]
    assert len(set(names)) == len(names), f"Duplicate history file names: {names}"
