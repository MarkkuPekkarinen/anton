"""Scenario C — Real tool execution paths."""

from __future__ import annotations

import json

from tests.e2e.harness import (
    assert_exit_ok, assert_not_output, assert_output, base_env, run_anton,
)


def test_scratchpad_exec_produces_real_output(cfg, stub, tmp_path):
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "main", "code": "print(12345)"})
    stub.queue_text("The answer is 12345. EXEC_CONFIRMED")
    stub.queue_verification_ok()
    user_msg = "Please use the scratchpad tool to run: print(12345)" if cfg.live else "compute 12345"
    result = run_anton(["--folder", str(tmp_path)], [user_msg, "exit"],
                       env=base_env(stub), timeout=cfg.timeout(30))
    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "12345" if cfg.live else "EXEC_CONFIRMED")


def test_tool_result_forwarded_to_llm(cfg, stub, tmp_path):
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "probe", "code": "print('PROBE_MARKER_99')"})
    stub.queue_text("I can see PROBE_MARKER_99. PROBE_SEEN")
    stub.queue_verification_ok()
    user_msg = (
        "Use the scratchpad to run: print('PROBE_MARKER_99') then tell me the output."
        if cfg.live else "run probe"
    )
    result = run_anton(["--folder", str(tmp_path)], [user_msg, "exit"],
                       env=base_env(stub), timeout=cfg.timeout(30))
    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "PROBE_MARKER_99")
    if not cfg.live:
        # Verify PROBE_MARKER_99 appears in a tool-result message in any follow-up request,
        # rather than pinning to reqs[1] which assumes a fixed request ordering.
        all_messages = json.dumps([r.get("messages", []) for r in stub.requests])
        assert "PROBE_MARKER_99" in all_messages, \
            f"Tool result not forwarded to LLM. Request count: {stub.request_count}"


def test_view_after_exec(cfg, stub, tmp_path):
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "nb", "code": "x = 777\nprint(x)"})
    stub.queue_text("OK, executed. VIEW_NEXT")
    stub.queue_verification_ok()
    stub.queue_tool_call("scratchpad", {"action": "view", "name": "nb"})
    stub.queue_text("Here is the notebook. VIEW_DONE")
    stub.queue_verification_ok()
    if cfg.live:
        inputs = ["Use the scratchpad to run: x = 777; print(x)",
                  "Now view the scratchpad notebook you just used", "exit"]
    else:
        inputs = ["exec the notebook", "now view it", "exit"]
    result = run_anton(["--folder", str(tmp_path)], inputs,
                       env=base_env(stub), timeout=cfg.timeout(35))
    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "777" if cfg.live else "VIEW_DONE")


def test_syntax_error_in_tool_does_not_kill_session(cfg, stub, tmp_path):
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "bad", "code": "def oops(:\n    pass"})
    stub.queue_text("There was an error but I survived. SURVIVED_ERROR")
    stub.queue_verification_ok()
    user_msg = (
        "Use the scratchpad to run: def oops(:\n    pass\nAfter the error say SURVIVED_ERROR"
        if cfg.live else "run bad code"
    )
    result = run_anton(["--folder", str(tmp_path)], [user_msg, "exit"],
                       env=base_env(stub), timeout=cfg.timeout(30))
    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "SURVIVED_ERROR")


def test_scratchpad_reset_clears_state(cfg, stub, tmp_path):
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "pad", "code": "result = 'before_reset'"})
    stub.queue_text("Executed. Resetting now.")
    stub.queue_verification_ok()
    stub.queue_tool_call("scratchpad", {"action": "reset", "name": "pad"})
    stub.queue_text("Reset done. RESET_CONFIRMED")
    stub.queue_verification_ok()
    if cfg.live:
        inputs = ["Use the scratchpad 'pad' to run: result = 'before_reset'",
                  "Now reset the scratchpad 'pad' and say RESET_CONFIRMED", "exit"]
    else:
        inputs = ["exec the pad", "now reset it", "exit"]
    result = run_anton(["--folder", str(tmp_path)], inputs,
                       env=base_env(stub), timeout=cfg.timeout(35))
    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "RESET_CONFIRMED")


def test_two_sequential_tool_rounds_no_hang(cfg, stub, tmp_path):
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "r1", "code": "print('ROUND_ONE')"})
    stub.queue_tool_call("scratchpad", {"action": "exec", "name": "r2", "code": "print('ROUND_TWO')"})
    stub.queue_text("Both rounds done. TWO_ROUNDS_DONE")
    stub.queue_verification_ok()
    user_msg = (
        "Use the scratchpad: run print('ROUND_ONE') in 'r1', then print('ROUND_TWO') in 'r2'. "
        "After both say TWO_ROUNDS_DONE"
        if cfg.live else "do two rounds"
    )
    result = run_anton(["--folder", str(tmp_path)], [user_msg, "exit"],
                       env=base_env(stub), timeout=cfg.timeout(40))
    assert_exit_ok(result)
    assert_not_output(result, "Traceback (most recent call last)")
    assert_output(result, "TWO_ROUNDS_DONE")
