"""The render-check client and the three places that use it.

A local HTTP server stands in for the render-check service, so every test
sees the exact request anton sends (path, content type, JSON body) and the
client's handling of each answer the contract allows: a verdict, an error
status, a dropped connection, a slow answer, a body that does not parse.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from anton.core.artifacts import ArtifactStore, render_check
from anton.core.artifacts.render_check import (
    RenderCheckRequest,
    RenderCheckResult,
    RenderCheckUnavailable,
    check_file,
    check_html,
)
from anton.core.backends.sandbox import CellSandbox
from anton.core.tools import tool_handlers
from anton.core.tools.generate_artifact import orchestrator, verifiers
from anton.core.tools.generate_artifact.state import GenState

_PAGE = (
    '<!doctype html><html><head><meta name="viewport" content="width=device-width">'
    '</head><body><div id="a">hi</div></body></html>'
)
_SHOT = base64.b64encode(b"\xff\xd8\xff\xe0 not really a jpeg").decode()


def _answer(status: str = "passed", *, findings=(), unreachable=(), screenshot: bool = True) -> dict:
    return {
        "status": status,
        "findings": list(findings),
        "unreachable": list(unreachable),
        "screenshot": {"media_type": "image/jpeg", "data": _SHOT} if screenshot else None,
        "policy": "default-src 'self'",
    }


class _Service:
    """What the fake service received, and what it answers next."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        # Each answer: (HTTP status, body bytes, delay in seconds). The last
        # one repeats once the list runs out.
        self.answers: list[tuple[int, bytes, float]] = [(200, json.dumps(_answer()).encode(), 0.0)]
        # When set, answers each request from its JSON body instead.
        self.respond = None

    def answer(self, *answers) -> None:
        self.answers = [
            (code, body if isinstance(body, bytes) else json.dumps(body).encode(), delay)
            for code, body, delay in answers
        ]


@pytest.fixture
def service(monkeypatch):
    state = _Service()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - the stdlib's name
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            state.requests.append({
                "path": self.path,
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(raw),
            })
            if state.respond is not None:
                code, body, delay = state.respond(state.requests[-1]["body"])
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
            else:
                code, body, delay = state.answers.pop(0) if len(state.answers) > 1 else state.answers[0]
            if delay:
                time.sleep(delay)
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client stopped reading an answer over its cap

        def log_message(self, *args):  # keep pytest output clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTON_RENDER_CHECK_URL", f"http://127.0.0.1:{server.server_address[1]}/")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The person's project, where the sandboxed tests keep their pages."""
    path = tmp_path / "project"
    path.mkdir()
    return path


@pytest.fixture
def policy(tmp_path: Path, project: Path) -> CellSandbox:
    for name in ("home", "tmp"):
        (tmp_path / name).mkdir()
    return CellSandbox(
        project_dir=project, home_dir=tmp_path / "home", tmp_dir=tmp_path / "tmp", user_name="bob",
    )


# ── The client ───────────────────────────────────────────────────────────────


def test_the_request_is_the_contract_body_posted_to_v1_check(service):
    result = check_html(_PAGE)

    assert service.requests == [{
        "path": "/v1/check",
        "content_type": "application/json",
        "body": {"html": _PAGE, "screenshot": True},
    }]
    assert isinstance(result, RenderCheckResult)
    assert result.status == "passed"
    assert result.screenshot is not None and result.screenshot.data == _SHOT


def test_a_check_without_a_screenshot_asks_for_none(service):
    check_html(_PAGE, screenshot=False)
    assert service.requests[0]["body"] == {"html": _PAGE, "screenshot": False}


def test_the_request_model_is_the_contract_and_nothing_more():
    assert RenderCheckRequest(html="x").model_dump() == {"html": "x", "screenshot": True}
    with pytest.raises(ValueError):
        RenderCheckRequest(html="x", extra=1)


def test_a_failed_verdict_keeps_every_finding(service):
    findings = [
        {"kind": "csp_violation", "viewport": "desktop", "detail": "Refused to load https://unpkg.com/leaflet.js"},
        {"kind": "uncaught_exception", "viewport": "phone", "detail": "ReferenceError: L is not defined"},
    ]
    service.answer((200, _answer("failed", findings=findings), 0.0))

    result = check_html(_PAGE)

    assert result.status == "failed"
    assert [(f.kind, f.viewport) for f in result.findings] == [
        ("csp_violation", "desktop"), ("uncaught_exception", "phone"),
    ]


@pytest.mark.parametrize("code,needle", [
    (503, "busy or not ready (HTTP 503)"),
    (413, "size limit (HTTP 413)"),
    (400, "rejected the request (HTTP 400)"),
    (500, "answered HTTP 500"),
])
def test_an_error_status_is_unverified_with_its_reason(service, code, needle):
    service.answer((code, {"error": "x"}, 0.0))
    result = check_html(_PAGE)
    assert isinstance(result, RenderCheckUnavailable)
    assert result.status == "unverified"
    assert needle in result.unverified_reason


def test_a_body_that_does_not_parse_is_unverified_never_a_pass(service):
    service.answer((200, b'{"status": "passed", "findings": "nope"}', 0.0))
    result = check_html(_PAGE)
    assert result.status == "unverified"
    assert "does not parse" in result.unverified_reason


def test_a_finding_kind_outside_the_contract_does_not_parse(service):
    service.answer((200, _answer("failed", findings=[{"kind": "new_kind", "viewport": "desktop", "detail": "x"}]), 0.0))
    assert check_html(_PAGE).status == "unverified"


def test_a_screenshot_that_is_not_base64_does_not_parse(service):
    body = _answer()
    body["screenshot"]["data"] = "not base64 at all!"
    service.answer((200, body, 0.0))
    assert check_html(_PAGE).status == "unverified"


def test_an_oversized_screenshot_is_dropped_but_the_verdict_kept(service, monkeypatch):
    monkeypatch.setattr(render_check, "MAX_SCREENSHOT_BASE64_CHARS", 4)
    result = check_html(_PAGE)
    assert result.status == "passed" and result.screenshot is None


def test_a_slow_service_is_unverified_after_the_timeout(service):
    service.answer((200, _answer(), 2.0))
    started = time.monotonic()
    result = check_html(_PAGE, timeout=0.3)
    assert time.monotonic() - started < 1.5
    assert result.status == "unverified"
    assert "no answer within" in result.unverified_reason


def test_a_service_nobody_listens_on_is_unverified(monkeypatch):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens there now: the connection is refused
    monkeypatch.setenv("ANTON_RENDER_CHECK_URL", f"http://127.0.0.1:{port}")
    result = check_html(_PAGE)
    assert result.status == "unverified"
    assert "could not be reached (ConnectError)" in result.unverified_reason


def test_unset_asks_nobody(monkeypatch):
    monkeypatch.delenv("ANTON_RENDER_CHECK_URL", raising=False)
    result = check_html(_PAGE)
    assert result.status == "unverified"
    assert "ANTON_RENDER_CHECK_URL unset" in result.unverified_reason


def test_a_page_over_the_size_limit_is_not_sent(service, monkeypatch):
    monkeypatch.setattr(render_check, "MAX_BODY_BYTES", 100)
    result = check_html(_PAGE * 3)
    assert result.status == "unverified" and "over the render check's" in result.unverified_reason
    assert service.requests == []


def test_the_service_is_reached_directly_whatever_proxy_the_process_has(service, monkeypatch):
    """The service sits on an internal network; the egress proxy a
    deployment sets for everything else would not reach it."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert check_html(_PAGE).status == "passed"
    assert len(service.requests) == 1


def test_an_unverified_answer_says_what_could_not_load(service):
    service.answer((200, _answer("unverified", unreachable=[
        {"url": "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js",
         "reason": "pin an exact version the check carries: echarts@5.5.1"},
    ], screenshot=False), 0.0))
    result = check_html(_PAGE)
    assert result.unverified_reason == (
        "could not load https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js "
        "(pin an exact version the check carries: echarts@5.5.1)"
    )


def test_check_file_reads_the_page_and_sends_its_text(service, tmp_path):
    page = tmp_path / "index.html"
    page.write_text(_PAGE)
    assert check_file(page, cell_sandbox=None).status == "passed"
    assert service.requests[0]["body"]["html"] == _PAGE


def test_check_file_never_follows_a_link_out_of_the_persons_files(service, tmp_path, project, policy):
    """This process runs outside the sandbox: a page linked to another
    person's file must not be read and shown back as a screenshot."""
    outside = tmp_path / "other-person.html"
    outside.write_text("<html><body>someone else's data</body></html>")
    link = project / "index.html"
    os.symlink(outside, link)
    result = check_file(link, cell_sandbox=policy)
    assert result.status == "unverified"
    assert "outside the files this conversation may read" in result.unverified_reason
    assert service.requests == []


def _swap_after_the_check(monkeypatch, swap) -> None:
    """Run `swap` right after the policy approves a path: agent code racing
    the host between the check and the read."""
    approve = CellSandbox.allows_read

    def racing(self, path):
        allowed = approve(self, path)
        swap()
        return allowed

    monkeypatch.setattr(CellSandbox, "allows_read", racing)


def test_a_link_swapped_in_after_the_check_is_not_followed(service, tmp_path, project, policy, monkeypatch):
    """The page was checked by name and then read by name, so a thread in
    the agent's kernel that swaps the page for a link between the two had
    the host read the link's target and send it to the service."""
    secret = tmp_path / "other-person.html"
    secret.write_text("<html><body>someone else's data</body></html>")
    page = project / "index.html"
    page.write_text(_PAGE)

    def swap():
        link = project / "swap.tmp"
        os.symlink(secret, link)
        os.replace(link, page)

    _swap_after_the_check(monkeypatch, swap)
    result = check_file(page, cell_sandbox=policy)

    assert result.status == "unverified"
    assert service.requests == []


def test_a_folder_swapped_for_a_link_after_the_check_is_not_followed(service, tmp_path, project, policy, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "index.html").write_text("<html><body>someone else's data</body></html>")
    site = project / "site"
    site.mkdir()
    (site / "index.html").write_text(_PAGE)

    def swap():
        os.rename(site, project / "site-old")
        os.symlink(elsewhere, site)

    _swap_after_the_check(monkeypatch, swap)
    result = check_file(site / "index.html", cell_sandbox=policy)

    assert result.status == "unverified"
    assert service.requests == []


def test_a_hard_link_to_another_persons_file_is_not_read(service, tmp_path, project, policy):
    """A hard link is another name for the same file: its resolved path
    sits in the project, but its bytes are someone else's."""
    secret = tmp_path / "other-person.html"
    secret.write_text("<html><body>someone else's data</body></html>")
    page = project / "index.html"
    os.link(secret, page)

    result = check_file(page, cell_sandbox=policy)

    assert result.status == "unverified"
    assert service.requests == []


def test_a_fifo_at_the_name_neither_hangs_nor_is_sent(service, project, policy):
    """Reading a FIFO with no writer never returns, which held the cell and
    a thread of the server forever."""
    page = project / "index.html"
    os.mkfifo(page)
    outcome: list = []
    worker = threading.Thread(target=lambda: outcome.append(check_file(page, cell_sandbox=policy)), daemon=True)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive(), "check_file hung on a FIFO"
    assert outcome[0].status == "unverified"
    assert outcome[0].unverified_reason == "the page could not be read (refused a file that is not a regular file)"
    assert service.requests == []


def test_a_name_that_cannot_be_resolved_is_unverified_not_raised(service, project, policy, monkeypatch):
    """Resolving a name agent code is swapping can raise (macOS: EINVAL
    from readlink); `check_file` never raises."""
    def failing(path):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(render_check.os.path, "realpath", failing)
    result = check_file(project / "index.html", cell_sandbox=policy)
    assert result.status == "unverified"
    assert result.unverified_reason == "the page could not be read (Invalid argument)"
    assert service.requests == []

def test_an_answer_over_the_response_cap_is_unverified(service):
    """anton used to read the whole answer, whatever its size, into a
    process every person on the server shares."""
    detail = "x" * (64 * 1024)
    service.answer((200, _answer("failed", findings=[
        {"kind": "console_error", "viewport": "desktop", "detail": f"{i} {detail}"} for i in range(80)
    ]), 0.0))

    result = check_html(_PAGE)

    assert isinstance(result, RenderCheckUnavailable)
    assert result.reason == "the render check's answer is over 4 MB, more than anton reads"


def _flood(count: int) -> dict:
    """A page that logs one error per data row."""
    return _answer("failed", findings=[
        {"kind": "console_error", "viewport": "desktop", "detail": f"missing value for row {i}"} for i in range(count)
    ])


def test_a_page_with_thousands_of_findings_reaches_the_agent_bounded(service, tmp_path):
    """Every distinct finding used to become a line of the cell's result and
    a verifier error of the retry prompt: 10,000 rows, 10,000 lines."""
    service.answer((200, _flood(10_000), 0.0))
    store, slug = _artifact_with_pages(tmp_path, "index.html")

    lint = tool_handlers.lint_changed_artifact_files(store, {slug: 0.0})

    shown = render_check.MAX_FINDINGS_SHOWN
    assert len(lint.messages) == shown + 1
    assert lint.messages[0] == f"{slug}/index.html — console_error (desktop): missing value for row 0"
    assert lint.messages[-1] == f"{slug}/index.html — and {10_000 - shown} more distinct findings, not listed"

    check = verifiers.verify_frontend_live(store.root / slug / "index.html", cell_sandbox=None)
    assert check.status == "failed"
    assert len(check.verdict.errors) == shown + 1
    assert check.verdict.errors[-1] == (
        f"Loaded in a headless browser, the page had {10_000 - shown} more distinct problems, "
        "not listed here: fix the ones above first."
    )


def test_the_url_never_reaches_agent_code(policy, monkeypatch):
    """The service holds no secret, but its address is the server's to use:
    a cell has no business calling it."""
    env = policy.child_env({"ANTON_RENDER_CHECK_URL": "http://render-check:8000", "PATH": "/usr/bin"})
    assert "ANTON_RENDER_CHECK_URL" not in env
    assert env["PATH"] == "/usr/bin"


# ── generate_artifact's browser step ─────────────────────────────────────────


def test_the_service_loads_the_page_even_under_a_sandbox_policy(service, project, policy):
    page = project / "index.html"
    page.write_text(_PAGE)

    check = verifiers.verify_frontend_live(page, cell_sandbox=policy)

    assert check.status == "passed"
    assert check.screenshot is not None
    assert service.requests[0]["body"] == {"html": _PAGE, "screenshot": True}


def test_failed_findings_become_verifier_errors_named_once_per_finding(service, tmp_path):
    page = tmp_path / "index.html"
    page.write_text(_PAGE)
    service.answer((200, _answer("failed", findings=[
        {"kind": "csp_violation", "viewport": "desktop", "detail": "script-src blocked https://unpkg.com/x.js"},
        {"kind": "csp_violation", "viewport": "phone", "detail": "script-src blocked https://unpkg.com/x.js"},
        {"kind": "separate_file", "viewport": "desktop", "detail": "data.js"},
        {"kind": "blank_view", "viewport": "phone", "detail": "#map"},
        {"kind": "overflow", "viewport": "phone", "detail": "scrollWidth 512 > 390"},
        {"kind": "uncaught_exception", "viewport": "desktop", "detail": "ReferenceError: L is not defined"},
        {"kind": "console_error", "viewport": "desktop", "detail": "boom"},
        {"kind": "crashed", "viewport": "desktop", "detail": "renderer gone"},
    ]), 0.0))

    check = verifiers.verify_frontend_live(page, cell_sandbox=None)

    assert check.status == "failed"
    assert check.verdict.errors == [
        "Loaded in a headless browser, the page broke the app preview's content security policy "
        "(desktop and phone): script-src blocked https://unpkg.com/x.js",
        "Loaded in a headless browser, the page asked for a separate file, which the app preview "
        "never serves (desktop): data.js",
        "Loaded in a headless browser, a chart, map or canvas stayed blank: #map (phone)",
        "Loaded in a headless browser, the page is wider than the screen: scrollWidth 512 > 390 (phone)",
        "Loaded in a headless browser, the page threw an uncaught exception: "
        "ReferenceError: L is not defined (desktop)",
        "Loaded in a headless browser, the page logged a console error: boom (desktop)",
        "Loaded in a headless browser, the page crashed the renderer process.",
    ]


def test_an_unverified_answer_is_a_skip_with_its_reason_not_an_error(service, tmp_path):
    page = tmp_path / "index.html"
    page.write_text(_PAGE)
    service.answer((503, {"error": "busy"}, 0.0))
    check = verifiers.verify_frontend_live(page, cell_sandbox=None)
    assert check.status == "unverified"
    assert check.reason == "unverified: the render-check service is busy or not ready (HTTP 503)"
    assert check.verdict.errors == []


def _gen_state(tmp_path: Path, policy=None) -> GenState:
    session = SimpleNamespace(_scratchpads=SimpleNamespace(cell_sandbox=policy), _page_checks={})
    return GenState(
        session=session, artifact_type="html-app", artifact_path=tmp_path, slug="dash",
        brief="b", is_fullstack=False,
    )


def _writes_page(tmp_path: Path, kickoffs: list[str]):
    async def fake_loop(**kw):
        kickoffs.append(kw["kickoff"])
        (tmp_path / "index.html").write_text(_PAGE)
        return {"files_written": ["index.html"], "rounds_used": 1, "summary": "s"}

    return fake_loop


async def test_the_generator_retries_on_what_the_service_found(service, project, policy, monkeypatch):
    """Under the sandbox, the preview's verdict feeds the same retry as the
    static rules, and the second attempt passes."""
    st = _gen_state(project, policy)
    kickoffs: list[str] = []
    monkeypatch.setattr(orchestrator.engine, "_run_loop", _writes_page(project, kickoffs))
    service.answer(
        (200, _answer("failed", findings=[
            {"kind": "csp_violation", "viewport": "desktop", "detail": "blocked https://unpkg.com/leaflet.js"},
        ]), 0.0),
        (200, _answer("passed"), 0.0),
    )

    assert await orchestrator._gen_verify_frontend(st) is None

    assert len(kickoffs) == 2
    assert "blocked https://unpkg.com/leaflet.js" in kickoffs[1]
    assert st.browser_status == "passed"
    assert st.browser_screenshot is not None
    assert st.checks_skipped == []
    shell = orchestrator._result_shell(st)
    assert shell["verified_in_browser"] is True
    assert shell["screenshot"] is st.browser_screenshot


async def test_an_unverified_page_passes_the_step_and_says_why(service, project, policy, monkeypatch):
    st = _gen_state(project, policy)
    monkeypatch.setattr(orchestrator.engine, "_run_loop", _writes_page(project, []))
    service.answer((200, _answer("unverified", unreachable=[
        {"url": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js", "reason": "not in the check's mirror"},
    ], screenshot=False), 0.0))

    assert await orchestrator._gen_verify_frontend(st) is None

    assert st.checks_skipped == [
        "browser load of the page: unverified: could not load "
        "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js (not in the check's mirror)"
    ]
    assert st.browser_status == "unverified"
    assert orchestrator._result_shell(st)["verified_in_browser"] is False


async def test_an_attached_image_is_embedded_so_the_page_passes(service, project, policy, monkeypatch):
    """An html-app with an attached logo: the generator writes
    `<img src="logo.png">` as told, and the preview never serves a file next
    to the page. The pipeline embeds the bytes before the check, so the
    check passes on the first attempt instead of failing every retry."""
    from anton.core.tools.generate_artifact import attachments as att_mod

    upload = project / "upload" / "logo.png"
    upload.parent.mkdir()
    upload.write_bytes(b"\x89PNG fake logo")
    st = _gen_state(project, policy)
    st.attachments = [att_mod.Attachment(path=str(upload), name="logo.png", size=15, kind=att_mod.KIND_ASSET)]
    orchestrator._stage_attachments(st)
    page = _PAGE.replace("<div", '<img src="logo.png" alt="logo"><div')

    kickoffs: list[str] = []

    async def fake_loop(**kw):
        kickoffs.append(kw["kickoff"])
        (project / "index.html").write_text(page)
        return {"files_written": ["index.html"], "rounds_used": 1, "summary": "s"}

    def respond(body):
        if 'src="logo.png"' in body["html"]:
            return 200, _answer("failed", findings=[
                {"kind": "separate_file", "viewport": "desktop", "detail": "logo.png"},
            ]), 0.0
        return 200, _answer("passed"), 0.0

    monkeypatch.setattr(orchestrator.engine, "_run_loop", fake_loop)
    service.respond = respond

    assert await orchestrator._gen_verify_frontend(st) is None

    assert len(kickoffs) == 1
    assert st.browser_status == "passed"
    sent = service.requests[0]["body"]["html"]
    assert 'src="data:image/png;base64,' + base64.b64encode(b"\x89PNG fake logo").decode() + '"' in sent
    assert (project / "index.html").read_text() == sent


def test_embedding_reads_no_link_out_of_the_persons_files(tmp_path, project, policy):
    """The staged copy sits where agent code writes, and its bytes land in a
    page the agent reads back: a link put there must not carry another
    person's file into the page."""
    from anton.core.tools.generate_artifact import attachments as att_mod

    secret = tmp_path / "other-person.png"
    secret.write_bytes(b"someone else's file")
    os.symlink(secret, project / "logo.png")
    att = att_mod.Attachment(path="x", name="logo.png", size=1, kind=att_mod.KIND_ASSET, staged="logo.png")
    html = '<img src="logo.png">'

    assert att_mod.embed_assets(html, [att], project, cell_sandbox=policy) == html


def test_embedding_covers_the_reference_shapes_and_leaves_text_alone(tmp_path):
    from anton.core.tools.generate_artifact import attachments as att_mod

    (tmp_path / "logo.png").write_bytes(b"png")
    (tmp_path / "font.woff2").write_bytes(b"font")
    atts = [
        att_mod.Attachment(path="x", name="logo.png", size=3, kind=att_mod.KIND_ASSET, staged="logo.png"),
        att_mod.Attachment(path="y", name="font.woff2", size=4, kind=att_mod.KIND_ASSET, staged="font.woff2"),
    ]
    png = "data:image/png;base64," + base64.b64encode(b"png").decode()
    html = (
        "<img src=\"logo.png\"><img src='./logo.png'><img src=logo.png>"
        "<style>@font-face{src:url(font.woff2)} .a{background:url( './logo.png' )}</style>"
        "<script>img.src = 'logo.png'</script><p>see logo.png</p>"
    )

    out = att_mod.embed_assets(html, atts, tmp_path, cell_sandbox=None)

    assert out.count(png) == 5
    assert "src:url(data:font/woff2;base64," + base64.b64encode(b"font").decode() + ")" in out
    assert "<p>see logo.png</p>" in out


async def test_the_browser_line_shows_only_where_a_browser_will_load_the_page(project, policy, monkeypatch):
    """`Loading the page in a browser` claims a browser: under a sandbox with
    no service none runs, so the line must not appear."""
    import asyncio

    monkeypatch.delenv("ANTON_RENDER_CHECK_URL", raising=False)
    monkeypatch.setattr(orchestrator.engine, "_run_loop", _writes_page(project, []))
    st = _gen_state(project, policy)
    st.progress = asyncio.Queue()

    assert await orchestrator._gen_verify_frontend(st) is None
    lines = []
    while not st.progress.empty():
        lines.append(st.progress.get_nowait())
    assert not [line for line in lines if "browser" in line]
    assert st.checks_skipped == [
        "browser load of the page: skipped: agent code runs in a sandbox on this deployment, "
        "which starts no browser"
    ]

    monkeypatch.setenv("ANTON_RENDER_CHECK_URL", "http://127.0.0.1:9")
    st = _gen_state(project, policy)
    st.progress = asyncio.Queue()
    await orchestrator._gen_verify_frontend(st)
    lines = []
    while not st.progress.empty():
        lines.append(st.progress.get_nowait())
    assert "Loading the page in a browser" in lines


async def test_generate_records_how_it_left_the_page(tmp_path, monkeypatch):
    from anton.core.tools.generate_artifact import engine

    session = SimpleNamespace(_page_checks={})
    st = SimpleNamespace(
        slug="dash", browser_status=None, files_written=["index.html"], is_fullstack=False, primary="index.html",
    )
    engine._record_page_check(session, st, "Frontend verification failed after 2 attempt(s): x")
    assert session._page_checks == {("dash", "index.html"): "unverified"}

    st.browser_status = "failed"
    engine._record_page_check(session, st, "Frontend verification failed after 2 attempt(s): x")
    assert session._page_checks == {("dash", "index.html"): "failed"}

    st.is_fullstack, st.browser_status = True, "passed"
    engine._record_page_check(session, st, {"status": "generated"})
    assert session._page_checks[("dash", "static/index.html")] == "passed"

    session._page_checks.clear()
    engine._record_page_check(session, st, {"status": "needs_confirmation"})
    st.files_written, st.browser_status = [], None
    engine._record_page_check(session, st, "Error: refused")
    assert session._page_checks == {}


# ── generate_artifact's result ───────────────────────────────────────────────


async def _handler_result(tmp_path: Path, monkeypatch, result: dict):
    from anton.core.tools import generate_artifact as gen_pkg

    store = ArtifactStore(tmp_path / "artifacts")
    slug = store.create(name="Map", description="d", type="html-app", primary="index.html").slug

    async def fake_generate(**kw):
        return dict(result)

    monkeypatch.setattr(gen_pkg, "generate", fake_generate)
    session = SimpleNamespace(_workspace=SimpleNamespace(artifacts_dir=tmp_path / "artifacts"))
    out = None
    async for item in tool_handlers.handle_generate_artifact(
        session, {"slug": slug, "user_request": "a map", "agent_understanding": "a map"}
    ):
        out = item
    return slug, out


async def test_the_screenshot_reaches_the_agent_beside_the_result(tmp_path, monkeypatch):
    shot = render_check.RenderScreenshot(media_type="image/jpeg", data=_SHOT)
    slug, out = await _handler_result(tmp_path, monkeypatch, {
        "status": "generated", "files_written": ["index.html"], "warnings": [], "checks_skipped": [],
        "verified_in_browser": True, "screenshot": shot,
    })

    assert out.ok is True
    text, caption, image = out.content
    payload = json.loads(text["text"])
    assert "screenshot" not in payload
    assert payload["verified_in_browser"] is True
    assert caption == {
        "type": "text",
        "text": f"[render check screenshot] {slug}/index.html as the app preview shows it, at desktop width.",
    }
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _SHOT}}


async def test_an_unverified_page_changes_what_the_agent_is_told(tmp_path, monkeypatch):
    """The old instruction said every file was verified and not to re-verify,
    which a sandboxed agent relayed as a working page."""
    _, out = await _handler_result(tmp_path, monkeypatch, {
        "status": "generated", "files_written": ["index.html"], "warnings": [],
        "checks_skipped": ["browser load of the page: unverified: the render-check service is busy"],
        "verified_in_browser": False,
    })
    instruction = json.loads(out.content)["instruction"]
    assert "unverified in a browser" in instruction
    assert "statically verified by the generation pipeline" not in instruction
    assert "Do NOT re-read, re-parse or re-verify" not in instruction


async def test_a_verified_page_keeps_the_do_not_reverify_instruction(tmp_path, monkeypatch):
    _, out = await _handler_result(tmp_path, monkeypatch, {
        "status": "generated", "files_written": ["index.html"], "warnings": [], "checks_skipped": [],
        "verified_in_browser": True,
    })
    instruction = json.loads(out.content)["instruction"]
    assert "Do NOT re-read, re-parse or re-verify" in instruction
    assert "unverified in a browser" not in instruction


# ── The check after a cell ───────────────────────────────────────────────────


def _artifact_with_pages(tmp_path: Path, *pages: str, primary: str = "index.html") -> tuple[ArtifactStore, str]:
    store = ArtifactStore(tmp_path / "artifacts")
    slug = store.create(name="Map", description="d", type="html-app", primary=primary).slug
    for name in pages:
        (store.root / slug / name).write_text(_PAGE.replace("hi", name))
    return store, slug


def test_only_edited_pages_are_sent_primary_first_with_one_screenshot(service, tmp_path):
    store, slug = _artifact_with_pages(tmp_path, "a.html", "z.html", primary="z.html")
    old = store.root / slug / "old.html"
    old.write_text(_PAGE)
    os.utime(old, (1, 1))
    before = {slug: 10.0}  # the artifact as it stood before the cell: only old.html

    lint = tool_handlers.lint_changed_artifact_files(store, before)

    sent = [r["body"] for r in service.requests]
    assert [b["html"] for b in sent] == [_PAGE.replace("hi", "z.html"), _PAGE.replace("hi", "a.html")]
    assert [b["screenshot"] for b in sent] == [True, False]
    assert lint.screenshot is not None and lint.screenshot_page == f"{slug}/z.html"
    assert lint.page_checks == {(slug, "z.html"): "passed", (slug, "a.html"): "passed"}
    assert lint.messages == [
        f"{slug}/z.html — loaded in a browser the way the app preview loads it: no problems found",
        f"{slug}/a.html — loaded in a browser the way the app preview loads it: no problems found",
    ]


def test_a_failed_page_lists_its_findings_and_wins_over_a_passed_one(service, tmp_path):
    store, slug = _artifact_with_pages(tmp_path, "index.html", "b.html")
    service.answer(
        (200, _answer("failed", findings=[
            {"kind": "separate_file", "viewport": "desktop", "detail": "data.js"},
            {"kind": "separate_file", "viewport": "phone", "detail": "data.js"},
        ]), 0.0),
        (200, _answer("passed", screenshot=False), 0.0),
    )
    lint = tool_handlers.lint_changed_artifact_files(store, {slug: 0.0})
    assert lint.messages[0] == f"{slug}/index.html — separate_file (desktop and phone): data.js"
    assert lint.page_checks == {(slug, "index.html"): "failed", (slug, "b.html"): "passed"}


def test_a_check_that_cannot_run_says_so_instead_of_nothing(service, project, policy):
    store, slug = _artifact_with_pages(project, "index.html")
    service.answer((503, {"error": "busy"}, 0.0))
    lint = tool_handlers.lint_changed_artifact_files(store, {slug: 0.0}, cell_sandbox=policy)
    assert lint.messages == [
        f"{slug}/index.html — not checked in a browser: the render-check service is busy or not ready (HTTP 503)"
    ]
    assert lint.page_checks == {(slug, "index.html"): "unverified"}
    assert lint.screenshot is None


def test_pages_past_the_cell_budget_are_named_as_not_checked(service, tmp_path, monkeypatch):
    monkeypatch.setattr(tool_handlers, "_RENDER_CHECK_CELL_BUDGET_SECONDS", 0.5)
    monkeypatch.setattr(tool_handlers, "_RENDER_CHECK_MIN_SECONDS", 0.4)
    store, slug = _artifact_with_pages(tmp_path, "index.html", "b.html")
    service.answer((200, _answer("passed"), 0.2))

    lint = tool_handlers.lint_changed_artifact_files(store, {slug: 0.0})

    assert len(service.requests) == 1
    assert lint.messages[-1] == (
        f"{slug}/b.html — not checked in a browser: the browser checks after this cell ran out of time before this page"
    )
    assert lint.page_checks == {(slug, "index.html"): "passed", (slug, "b.html"): "unverified"}


def test_the_screenshot_rides_a_successful_cell_only(tmp_path):
    shot = render_check.RenderScreenshot(media_type="image/jpeg", data=_SHOT)
    lint = tool_handlers.ArtifactLint(messages=["dash/index.html — ok"], screenshot=shot, screenshot_page="dash/index.html")

    content = tool_handlers.cell_content_with_lint("[output]\nhi", lint, cell_ok=True)
    assert content[0] == {"type": "text", "text": "[output]\nhi\n\n[artifact lint]\ndash/index.html — ok"}
    assert content[1]["text"].startswith(render_check.SCREENSHOT_CAPTION)
    assert content[2]["type"] == "image"

    failed = tool_handlers.cell_content_with_lint("[error]\nboom", lint, cell_ok=False)
    assert failed == "[error]\nboom\n\n[artifact lint]\ndash/index.html — ok"


def test_the_record_keeps_each_pages_latest_check(tmp_path):
    key = tool_handlers.PageKey
    session = SimpleNamespace(_page_checks={key("a", "index.html"): "passed"})
    tool_handlers.record_page_checks(session, {key("a", "index.html"): "unverified", key("b", "index.html"): "passed"})
    assert session._page_checks == {key("a", "index.html"): "unverified", key("b", "index.html"): "passed"}
    # A session without the record (a test double) is left alone.
    tool_handlers.record_page_checks(SimpleNamespace(), {key("a", "index.html"): "failed"})


def test_page_lines_name_each_artifact_with_a_page_that_did_not_pass(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    shipped = store.create(name="Shipments map", description="d", type="html-app").slug
    broken = store.create(name="Sales globe", description="d", type="html-app").slug
    key = tool_handlers.PageKey
    lines = tool_handlers.page_check_lines(store, {
        key(shipped, "index.html"): "unverified",
        key(broken, "index.html"): "failed",
        key(broken, "details.html"): "passed",
        key("ok", "index.html"): "passed",
    })
    assert lines == [
        "“Shipments map” was not verified in a browser, so it may not display correctly in the preview.",
        "“Sales globe” failed the browser check, so it may not display correctly in the preview.",
    ]


def test_a_later_pass_of_another_page_keeps_the_failed_one(service, tmp_path):
    """Two cells, two pages of one artifact: the first cell's failed
    index.html stays failed when the second cell edits only details.html and
    it passes. One record per artifact let that pass hide the failure the
    preview still shows."""
    store, slug = _artifact_with_pages(tmp_path, "index.html", "details.html")
    index, details = store.root / slug / "index.html", store.root / slug / "details.html"
    os.utime(details, (1, 1))
    session = SimpleNamespace(_page_checks={})
    service.answer(
        (200, _answer("failed", findings=[
            {"kind": "csp_violation", "viewport": "desktop", "detail": "style-src blocked leaflet.css"},
        ]), 0.0),
        (200, _answer("passed"), 0.0),
    )

    before = {slug: 10.0}
    tool_handlers.record_page_checks(session, tool_handlers.lint_changed_artifact_files(store, before).page_checks)
    os.utime(index, (20, 20))
    details.write_text(_PAGE)
    before = {slug: 20.0}
    tool_handlers.record_page_checks(session, tool_handlers.lint_changed_artifact_files(store, before).page_checks)

    assert [r["body"]["html"] for r in service.requests] == [_PAGE.replace("hi", "index.html"), _PAGE]
    assert tool_handlers.page_check_lines(store, session._page_checks) == [
        "“Map” failed the browser check, so it may not display correctly in the preview."
    ]


# ── Screenshots in history ───────────────────────────────────────────────────


def _result_with_shot(tool_use_id: str, data: str) -> dict:
    shot = render_check.RenderScreenshot(media_type="image/jpeg", data=data)
    return {
        "role": "user",
        "content": [{
            "type": "tool_result", "tool_use_id": tool_use_id,
            "content": [{"type": "text", "text": "[output]\n"},
                        *render_check.screenshot_blocks(shot, page="dash/index.html")],
        }],
    }


def test_only_the_newest_render_screenshot_stays_in_history():
    other = base64.b64encode(b"second").decode()
    read_image = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "img", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            {"type": "text", "text": "Loaded chart.png (10x10, 3 B)."},
        ]}],
    }
    history = [_result_with_shot("t1", _SHOT), read_image, _result_with_shot("t2", other)]

    assert render_check.drop_older_screenshots(history) == 1

    first = history[0]["content"][0]["content"]
    assert first[-1] == {"type": "text", "text": render_check.OLDER_SCREENSHOT_PLACEHOLDER}
    assert history[1] == read_image, "an image the agent read itself is not a render screenshot"
    assert history[2]["content"][0]["content"][-1]["source"]["data"] == other
    assert render_check.drop_older_screenshots(history) == 0


# ── The streaming turn: the product path ─────────────────────────────────────


class _FakeAsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _PagePad:
    """A scratchpad that, like the real one, writes the page to disk and
    hands back the cell. Records each code it ran."""

    def __init__(self, page: Path) -> None:
        self.page = page
        self.ran: list[str] = []

    async def install_packages(self, packages):  # pragma: no cover - unused
        return "nothing to install"

    async def execute_streaming(self, code, **kwargs):
        from anton.core.backends.base import Cell

        self.ran.append(code)
        self.page.write_text(_PAGE.replace("hi", f"cell {len(self.ran)}"))
        yield Cell(code=code, stdout="wrote the page", stderr="", error=None, description="write page")


def _llm_response(text: str, tool_input: dict | None = None, tool_id: str = "tc_1"):
    from anton.core.llm.provider import LLMResponse, ToolCall, Usage

    return LLMResponse(
        content=text,
        tool_calls=[ToolCall(id=tool_id, name="scratchpad", input=tool_input)] if tool_input else [],
        usage=Usage(input_tokens=10, output_tokens=20),
        stop_reason="tool_use" if tool_input else "end_turn",
    )


def _session_for(project: Path, replies: list, *, verdicts=()):
    """A ChatSession whose model plays `replies` in order and whose pad writes
    the artifact's page. Returns (session, pad, slug)."""
    from unittest.mock import AsyncMock, MagicMock

    from anton.core.llm.provider import StreamComplete, StreamTextDelta
    from anton.core.session import ChatSession, ChatSessionConfig, _VerifierVerdict
    from tests.conftest import make_mock_llm

    workspace = MagicMock(base=project)
    workspace.artifacts_dir = project / "artifacts"
    slug = ArtifactStore(workspace.artifacts_dir).create(
        name="Shipments map", description="d", type="html-app", primary="index.html"
    ).slug
    pad = _PagePad(workspace.artifacts_dir / slug / "index.html")

    mock_llm = make_mock_llm()
    pending = list(verdicts)

    async def verdict(*args, **kwargs):
        return pending.pop(0) if pending else _VerifierVerdict(status="COMPLETE", reason="done")

    mock_llm.generate_object_code = AsyncMock(side_effect=verdict)
    queue = list(replies)

    def plan_stream(**kwargs):
        reply = queue.pop(0) if queue else _llm_response("")
        events = [StreamTextDelta(text=reply.content)] if reply.content else []
        return _FakeAsyncIter([*events, StreamComplete(response=reply)])

    mock_llm.plan_stream = plan_stream
    session = ChatSession(ChatSessionConfig(llm_client=mock_llm, workspace=workspace))
    session._scratchpads.get_or_create = AsyncMock(return_value=pad)
    return session, pad, slug


def _tool_results(session) -> list:
    return [
        block
        for message in session.history if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"] if isinstance(block, dict) and block.get("type") == "tool_result"
    ]


async def test_the_cell_result_carries_the_screenshot_on_the_streaming_path(service, project, policy):
    session, _, slug = _session_for(project, [
        _llm_response("Writing.", {"action": "exec", "name": "main", "code": "write_page()"}),
        _llm_response("Done."),
    ])
    session._scratchpads._cell_sandbox = policy
    try:
        async for _ in session.turn_stream("draw the map"):
            pass
    finally:
        await session.close()

    assert [r["body"]["html"] for r in service.requests] == [_PAGE.replace("hi", "cell 1")]
    text, caption, image = _tool_results(session)[0]["content"]
    assert text["text"].endswith(
        f"[artifact lint]\n{slug}/index.html — loaded in a browser the way the app preview loads it: no problems found"
    )
    assert caption["text"].startswith(render_check.SCREENSHOT_CAPTION)
    assert image["source"]["data"] == _SHOT
    assert session._page_checks == {(slug, "index.html"): "passed"}


async def test_two_cells_leave_one_screenshot_in_history(service, project):
    second = base64.b64encode(b"second page").decode()
    service.answer((200, _answer(), 0.0), (200, {**_answer(), "screenshot": {"media_type": "image/jpeg", "data": second}}, 0.0))
    session, _, _ = _session_for(project, [
        _llm_response("One.", {"action": "exec", "name": "main", "code": "write_page()"}, "tc_1"),
        _llm_response("Two.", {"action": "exec", "name": "main", "code": "write_page()"}, "tc_2"),
        _llm_response("Done."),
    ])
    try:
        async for _ in session.turn_stream("draw the map"):
            pass
    finally:
        await session.close()

    images = [
        block["source"]["data"]
        for result in _tool_results(session) if isinstance(result["content"], list)
        for block in result["content"] if block.get("type") == "image"
    ]
    assert images == [second]
    first = _tool_results(session)[0]["content"]
    assert {"type": "text", "text": render_check.OLDER_SCREENSHOT_PLACEHOLDER} in first


async def _final_text(session, prompt: str = "draw the map") -> tuple[str, list]:
    from anton.core.llm.provider import StreamTextDelta

    events = []
    try:
        async for event in session.turn_stream(prompt):
            events.append(event)
    finally:
        await session.close()
    return "".join(e.text for e in events if isinstance(e, StreamTextDelta)), events


_LINE = "“Shipments map” was not verified in a browser, so it may not display correctly in the preview."


async def test_a_sandboxed_turn_ends_with_a_line_for_an_unverified_page(service, project, policy):
    """The model can claim the page works whatever the checks said; this line
    is anton's own, appended after the reply, and stays in history."""
    service.answer((503, {"error": "busy"}, 0.0))
    session, _, _ = _session_for(project, [
        _llm_response("Writing.", {"action": "exec", "name": "main", "code": "write_page()"}),
        _llm_response("The map is ready and works."),
    ])
    session._scratchpads._cell_sandbox = policy

    streamed, _ = await _final_text(session)

    assert streamed == "Writing.The map is ready and works." + _LINE
    last = session.history[-1]
    assert last["role"] == "assistant"
    texts = last["content"] if isinstance(last["content"], str) else "".join(b["text"] for b in last["content"])
    assert texts.endswith(_LINE)


async def test_a_page_that_passed_gets_no_line(service, project, policy):
    session, _, _ = _session_for(project, [
        _llm_response("Writing.", {"action": "exec", "name": "main", "code": "write_page()"}),
        _llm_response("The map is ready."),
    ])
    session._scratchpads._cell_sandbox = policy
    streamed, _ = await _final_text(session)
    assert streamed == "Writing.The map is ready."


async def test_without_a_sandbox_the_turn_gets_no_line(service, project):
    service.answer((503, {"error": "busy"}, 0.0))
    session, _, _ = _session_for(project, [
        _llm_response("Writing.", {"action": "exec", "name": "main", "code": "write_page()"}),
        _llm_response("Done."),
    ])
    streamed, _ = await _final_text(session)
    assert _LINE not in streamed


async def test_the_line_after_an_unspent_continuation_is_marked_as_no_replacement(
    service, project, policy, monkeypatch
):
    """A consumer takes the first text after a continuation boundary for the
    answer that replaces the one it showed. When the continuation said
    nothing, the line must not wipe the answer the user read: a hand-back
    marker goes first."""
    from anton.core.llm.provider import StreamTaskProgress, StreamTextDelta
    from anton.core.session import _VerifierVerdict

    # The completion check, which forces the continuation, skips turns of
    # fewer tool rounds than this.
    monkeypatch.setenv("ANTON_VERIFY_MIN_TOOL_ROUNDS", "1")

    service.answer((503, {"error": "busy"}, 0.0))
    session, _, _ = _session_for(project, [
        _llm_response("Writing.", {"action": "exec", "name": "main", "code": "write_page()"}),
        _llm_response("ANSWER"),
        _llm_response(""),
    ], verdicts=[_VerifierVerdict(status="INCOMPLETE", reason="not done")])
    session._scratchpads._cell_sandbox = policy

    _, events = await _final_text(session)

    phases = [(i, e.phase) for i, e in enumerate(events) if isinstance(e, StreamTaskProgress)]
    line = next(i for i, e in enumerate(events) if isinstance(e, StreamTextDelta) and e.text == _LINE)
    assert any(p == "continuation" for _, p in phases)
    assert events[line - 1] == StreamTaskProgress(phase="handback", message="")


# ── What the generator and the skill tell the model ──────────────────────────


def _skill() -> str:
    path = Path(__file__).resolve().parents[1] / "anton/core/memory/builtin_skills/build-html-dashboard/SKILL.md"
    return path.read_text(encoding="utf-8")


def test_the_skill_names_the_preview_rules_and_the_tested_recipe():
    skill = _skill()
    for rule in (
        "https://cdn.jsdelivr.net/npm/<package>@<exact version>/",
        "no external stylesheet",
        "No external images or map tiles",
        "No `eval`/`new Function` and no web workers",
        "d3@7.9.0 with topojson-client@3.1.0 and world-atlas@2.0.2",
        "echarts.registerMap(name, geoJson)",
        "globe.gl@2.34.2 with `polygonsData`",
        "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json",
        "@tailwindcss/browser@4.3.3",
        "echarts@5.5.1",
    ):
        assert rule in skill, rule


def test_the_skill_embeds_everything_and_no_longer_splits_out_big_data():
    """A sibling `dashboard_data.js` or image never loads in the preview."""
    skill = _skill()
    assert "Anything larger than 100KB is the exception" not in skill
    assert "shutil.copy(src, dest_dir / name)" not in skill
    assert "however large" in skill


def test_the_skill_says_what_checks_a_hand_built_page_now():
    skill = _skill()
    assert "a hand-built page gets no automatic check" not in skill
    assert "A hand-built page gets the browser check too" in skill
    assert "not checked in a browser" in skill
