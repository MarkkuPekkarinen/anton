"""Scenario G — Scratchpad resource exhaustion and resilience."""

from __future__ import annotations

import pytest

from tests.e2e.harness import (
    assert_exit_ok, assert_not_output, assert_output, base_env, run_anton,
)


def _tool_result_contents(requests: list[dict]) -> list[str]:
    """Return tool-role message contents from the most recent stub request that has any."""
    for req in reversed(requests):
        results = [
            msg.get("content", "")
            for msg in req.get("messages", [])
            if msg.get("role") == "tool"
        ]
        if results:
            return results
    return []


@pytest.mark.stub_only
def test_output_flooding_truncated(cfg, stub, tmp_path):
    flood_code = "for i in range(1_000_000):\n    print(i)\n"
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "flood", "code": flood_code})
    stub.queue_text("TRUNCATION_SEEN")
    stub.queue_verification_ok()
    result = run_anton(["--folder", str(tmp_path)], ["flood the output", "exit"],
                       env=base_env(stub), timeout=cfg.timeout(30))

    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "TRUNCATION_SEEN")
    tool_results = _tool_result_contents(stub.requests)
    assert tool_results, "No tool result message found in stub requests"
    flood_result = tool_results[0]
    # scratchpad._MAX_OUTPUT = 10_000; all truncation paths emit "... (truncated"
    assert "truncated" in flood_result, f"Truncation marker missing. Result: {flood_result[:500]!r}"
    assert len(flood_result) < 20_000, f"Tool result too large ({len(flood_result)} chars)"


@pytest.mark.stub_only
def test_progress_markers_not_leaked_to_llm(cfg, stub, tmp_path):
    progress_code = (
        "for i in range(3):\n"
        "    progress(f'step {i}')\n"
        "print('ALL_STEPS_DONE')\n"
    )
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "steps", "code": progress_code})
    stub.queue_text("PROGRESS_OK")
    stub.queue_verification_ok()
    result = run_anton(["--folder", str(tmp_path)], ["run with progress markers", "exit"],
                       env=base_env(stub), timeout=cfg.timeout(20))

    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "PROGRESS_OK")
    tool_results = _tool_result_contents(stub.requests)
    assert tool_results, "No tool result message found in stub requests"
    cell_result = tool_results[0]
    assert "ALL_STEPS_DONE" in cell_result, f"Expected ALL_STEPS_DONE in: {cell_result!r}"
    # _PROGRESS_MARKER = "__ANTON_PROGRESS__" is a stable internal constant (scratchpad.py:20)
    assert "__ANTON_PROGRESS__" not in cell_result, f"Progress marker leaked: {cell_result!r}"


def test_error_cell_subprocess_survives(cfg, stub, tmp_path):
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "test_pad", "code": "def broken(:\n    pass\n"})
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "test_pad", "code": "print('SURVIVED')\n"})
    stub.queue_text("RECOVERY_CONFIRMED")
    stub.queue_verification_ok()
    if cfg.live:
        user_msg = (
            "Use scratchpad 'test_pad' to run: def broken(:\n    pass\n"
            "After the error run: print('SURVIVED')\nThen say RECOVERY_CONFIRMED"
        )
    else:
        user_msg = "run two cells"
    result = run_anton(["--folder", str(tmp_path)], [user_msg, "exit"],
                       env=base_env(stub), timeout=cfg.timeout(30))

    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    if cfg.live:
        assert_output(result, "SURVIVED")
        assert_output(result, "RECOVERY_CONFIRMED")
    else:
        tool_results = _tool_result_contents(stub.requests)
        assert len(tool_results) >= 2, f"Expected >=2 tool results, got {len(tool_results)}"
        # scratchpad.py:633 — cell.error is formatted as "[error]\n{cell.error}" in view()
        assert "[error]" in tool_results[0], f"Expected [error] in first result: {tool_results[0]!r}"
        assert "SURVIVED" in tool_results[1], f"Expected SURVIVED in second result: {tool_results[1]!r}"
