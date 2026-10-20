"""Client for the render-check service: load a page the way the app preview does.

The service runs headless Chromium in its own container and loads the page
inside a sandboxed srcdoc frame under the preview's content security policy,
at a desktop and a phone width (cowork-self-hosted `render-check/`). anton
sends the page's text and reads back a verdict. Nothing runs in this process
and nothing of this process's environment is sent, so the check works where
agent code runs in a sandbox and no local browser may start.

The service's URL comes from `ANTON_RENDER_CHECK_URL` in this process's
environment, read at call time the way `html_lint` reads
`ANTON_HTML_LINT_BROWSER`. Unset, no caller asks the service anything.

Every way the service can fail to answer (not configured, unreachable, slow,
busy, an error status, a body that does not parse) comes back as
`RenderCheckUnavailable` with its reason, which callers treat as an
unverified page. Neither it nor a malformed answer ever reads as a pass.
"""

from __future__ import annotations

import base64
import binascii
import errno
import logging
import os
import time
from pathlib import Path
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, NamedTuple

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from anton.utils.datasources import scrub_output
from anton.utils.nofollow import read_bytes_nofollow

if TYPE_CHECKING:
    from anton.core.backends.sandbox import CellSandbox

_log = logging.getLogger(__name__)

RENDER_CHECK_ENV_VAR = "ANTON_RENDER_CHECK_URL"
# anton gives up on one check after this long. The service's own deadline
# for a whole check is shorter (25 s), so a slow page still comes back as a
# `timeout` finding before this fires.
TIMEOUT_SECONDS = 30.0
# The service refuses a larger body with 413; anton does not send one.
MAX_BODY_BYTES = 8 * 1024 * 1024
# anton stops reading an answer at this size and counts the page unverified.
# Room for the largest screenshot it keeps (`MAX_SCREENSHOT_BASE64_CHARS`)
# and many findings; a page that logs without end cannot make this process
# hold an answer of any size.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# At most this many distinct findings of one check reach the agent; the rest
# are counted. A page that logs one error per data row would otherwise put
# thousands of lines into one tool result, resent on every model call.
MAX_FINDINGS_SHOWN = 20
# A screenshot larger than this (base64 text) is not shown to the model. The
# service sends a JPEG at most 1024 px wide, far below it; the cap keeps a
# request with one screenshot well under the model route's 16 MB limit.
MAX_SCREENSHOT_BASE64_CHARS = 1024 * 1024

RenderStatus = Literal["passed", "failed", "unverified"]
FindingKind = Literal[
    "csp_violation",
    "separate_file",
    "console_error",
    "uncaught_exception",
    "blank_view",
    "overflow",
    "crashed",
    "timeout",
]


class RenderCheckRequest(BaseModel):
    """`POST /v1/check` body: the entry file's text as it sits on disk.

    The service injects the preview shim and the `<base>` itself.
    """

    model_config = ConfigDict(extra="forbid")

    html: str
    screenshot: bool = True


class RenderFinding(BaseModel):
    """One problem seen in one viewport."""

    model_config = ConfigDict(extra="ignore")

    kind: FindingKind
    viewport: Literal["desktop", "phone"]
    detail: str


class UnreachableResource(BaseModel):
    """A URL the check could not load, so it cannot say how the page renders with it."""

    model_config = ConfigDict(extra="ignore")

    url: str
    reason: str


class RenderScreenshot(BaseModel):
    """The desktop viewport of the frame, base64-encoded."""

    model_config = ConfigDict(extra="ignore")

    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: str

    @field_validator("data")
    @classmethod
    def _is_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("screenshot data is not base64") from exc
        return value

    def image_block(self) -> dict:
        """The screenshot as an image content block, the shape `read_image` returns."""
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": self.media_type, "data": self.data},
        }


class RenderCheckResult(BaseModel):
    """The service's 200 answer. The service computes `status`:

    1. `failed` on any `csp_violation` or `separate_file` finding;
    2. otherwise `unverified` when `unreachable` is not empty or a `timeout`
       finding exists;
    3. otherwise `failed` on any other finding;
    4. otherwise `passed`.
    """

    model_config = ConfigDict(extra="ignore")

    status: RenderStatus
    findings: list[RenderFinding] = Field(default_factory=list)
    unreachable: list[UnreachableResource] = Field(default_factory=list)
    screenshot: RenderScreenshot | None = None
    policy: str = ""

    @property
    def unverified_reason(self) -> str:
        """Why the check could not vouch for the page, in one line."""
        parts = [
            f"could not load {item.url} ({item.reason})" for item in self.unreachable[:3]
        ]
        if len(self.unreachable) > 3:
            parts.append(f"and {len(self.unreachable) - 3} more resources")
        parts += [
            f"the check ran out of time ({f.detail})" for f in self.findings if f.kind == "timeout"
        ][:1]
        return scrub_output("; ".join(parts) or "the render check could not verify the page")


class RenderCheckUnavailable(BaseModel):
    """No verdict from the service: the page counts as unverified."""

    model_config = ConfigDict(frozen=True)

    reason: str

    status: Literal["unverified"] = "unverified"

    @property
    def findings(self) -> list[RenderFinding]:
        return []

    @property
    def screenshot(self) -> None:
        return None

    @property
    def unverified_reason(self) -> str:
        return self.reason


RenderCheckOutcome = RenderCheckResult | RenderCheckUnavailable


class FindingGroup(NamedTuple):
    """One distinct finding and the viewports it was seen in."""

    kind: FindingKind
    detail: str
    # "desktop", "phone" or "desktop and phone".
    viewports: str


class GroupedFindings(NamedTuple):
    """The distinct findings to show, and how many more were left out."""

    shown: list[FindingGroup]
    more: int


def grouped_findings(findings: Sequence[RenderFinding]) -> GroupedFindings:
    """Each distinct finding once, in the order first seen, its detail scrubbed:
    the same problem in both viewports is one problem to fix. The first
    `MAX_FINDINGS_SHOWN` are shown and the rest counted."""
    seen: dict[tuple[str, str], list[str]] = {}
    for f in findings:
        seen.setdefault((f.kind, scrub_output(f.detail)), []).append(f.viewport)
    groups = [
        FindingGroup(kind=kind, detail=detail, viewports=" and ".join(dict.fromkeys(viewports)))
        for (kind, detail), viewports in seen.items()
    ]
    return GroupedFindings(shown=groups[:MAX_FINDINGS_SHOWN], more=max(len(groups) - MAX_FINDINGS_SHOWN, 0))


def configured_url() -> str | None:
    """The service's base URL, or None when `ANTON_RENDER_CHECK_URL` is unset or blank."""
    value = os.environ.get(RENDER_CHECK_ENV_VAR, "").strip()
    return value.rstrip("/") or None


def check_html(html: str, *, screenshot: bool = True, timeout: float = TIMEOUT_SECONDS) -> RenderCheckOutcome:
    """Ask the service to load `html`. Never raises.

    `timeout` is anton's limit for this one call; a caller with a smaller
    budget left passes what remains.
    """
    base = configured_url()
    if base is None:
        return RenderCheckUnavailable(reason=f"no render-check service configured ({RENDER_CHECK_ENV_VAR} unset)")
    body = RenderCheckRequest(html=html, screenshot=screenshot).model_dump_json().encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        return RenderCheckUnavailable(
            reason=f"the page is {len(body) // 1024} KB, over the render check's {MAX_BODY_BYTES // (1024 * 1024)} MB limit"
        )
    try:
        # trust_env=False: the service is a sibling container on an internal
        # network, never behind the egress proxy a deployment may set.
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            with client.stream(
                "POST", f"{base}/v1/check", content=body, headers={"Content-Type": "application/json"}
            ) as response:
                if response.status_code != 200:
                    return RenderCheckUnavailable(reason=_status_reason(response.status_code))
                content = bytearray()
                for chunk in response.iter_bytes():
                    content += chunk
                    if len(content) > MAX_RESPONSE_BYTES:
                        return RenderCheckUnavailable(
                            reason="the render check's answer is over "
                            f"{MAX_RESPONSE_BYTES // (1024 * 1024)} MB, more than anton reads"
                        )
    except httpx.TimeoutException:
        return RenderCheckUnavailable(reason=f"the render check gave no answer within {timeout:.0f} s")
    except httpx.HTTPError as exc:
        _log.warning("render check at %s could not be reached", base, exc_info=True)
        return RenderCheckUnavailable(reason=f"the render-check service could not be reached ({type(exc).__name__})")
    try:
        result = RenderCheckResult.model_validate_json(bytes(content))
    except ValidationError:
        _log.warning("render check at %s answered a body that does not parse", base, exc_info=True)
        return RenderCheckUnavailable(reason="the render-check service answered something that does not parse")
    if result.screenshot is not None and len(result.screenshot.data) > MAX_SCREENSHOT_BASE64_CHARS:
        result = result.model_copy(update={"screenshot": None})
    return result


def check_file(
    path: Path, *, cell_sandbox: CellSandbox | None, screenshot: bool = True, timeout: float = TIMEOUT_SECONDS
) -> RenderCheckOutcome:
    """Read the page at `path` and ask the service to load it. Never raises.

    This process reads the file outside the sandbox, so under a policy it
    reads only what the person's own cells may read. `allows_read` approves
    the page's resolved path, and `read_bytes_nofollow` then opens that path
    one folder at a time with O_NOFOLLOW, so the file read is the one
    approved: agent code that swaps a link in after the check makes the read
    fail instead of leading it out of the person's files. The read also
    refuses a FIFO, a device or a file with another hard link, and reads no
    more than the service accepts.
    """
    started = time.monotonic()
    try:
        # Inside the try: resolving a name that agent code is swapping can
        # itself fail.
        real = Path(os.path.realpath(path))
        if cell_sandbox is not None and not cell_sandbox.allows_read(real):
            return RenderCheckUnavailable(reason="the page is outside the files this conversation may read")
        data = read_bytes_nofollow(real.anchor, real, limit=MAX_BODY_BYTES)
    except OSError as exc:
        if exc.errno == errno.EFBIG:
            return RenderCheckUnavailable(
                reason=f"the page is over the render check's {MAX_BODY_BYTES // (1024 * 1024)} MB limit"
            )
        return RenderCheckUnavailable(reason=f"the page could not be read ({exc.strerror or type(exc).__name__})")
    remaining = timeout - (time.monotonic() - started)
    return check_html(data.decode("utf-8", errors="replace"), screenshot=screenshot, timeout=max(remaining, 1.0))


# Opens the caption beside every render screenshot in a tool result, so
# `drop_older_screenshots` can tell these images from one `read_image` returned.
SCREENSHOT_CAPTION = "[render check screenshot]"
OLDER_SCREENSHOT_PLACEHOLDER = (
    "[An older render-check screenshot was removed to keep the conversation small; "
    "the newest one is kept.]"
)
REFUSED_SCREENSHOT_PLACEHOLDER = (
    "[The model refused a request with this render-check screenshot in it, so the "
    "screenshot was removed. The findings above still apply, and later checks in this "
    "conversation send their findings without a screenshot.]"
)


def screenshot_blocks(screenshot: RenderScreenshot, *, page: str) -> list[dict]:
    """The caption and the image, to follow a tool result's text."""
    return [
        {
            "type": "text",
            "text": f"{SCREENSHOT_CAPTION} {page} as the app preview shows it, at desktop width.",
        },
        screenshot.image_block(),
    ]


def _is_render_result(blocks: list) -> bool:
    return any(
        isinstance(b, dict) and b.get("type") == "text"
        and str(b.get("text", "")).startswith(SCREENSHOT_CAPTION)
        for b in blocks
    )


def _render_results(messages: list[dict]) -> list[dict]:
    """The `tool_result` blocks in `messages` that carry a render screenshot's caption, oldest first."""
    found: list[dict] = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            inner = block.get("content")
            if isinstance(inner, list) and _is_render_result(inner):
                found.append(block)
    return found


def _replace_screenshots(sites: list[tuple[dict, int]], text: str) -> int:
    """Put a text block holding `text` in place of each image; return how many."""
    for block, index in sites:
        inner = list(block["content"])
        inner[index] = {"type": "text", "text": text}
        block["content"] = inner
    return len(sites)


def _screenshot_sites(messages: list[dict]) -> list[tuple[dict, int]]:
    """Each render screenshot in `messages`, as its `tool_result` block and
    the image's index inside it, oldest first."""
    return [
        (block, i)
        for block in _render_results(messages)
        for i, b in enumerate(block["content"]) if isinstance(b, dict) and b.get("type") == "image"
    ]


def drop_older_screenshots(messages: list[dict]) -> int:
    """Keep only the newest render screenshot in `messages`; return how many
    older ones were replaced with a short placeholder.

    A screenshot lives in a `tool_result` block next to its caption. Every
    check can add one, and the whole history is sent on every model call, so
    without this they would pile up towards the model route's size limit.
    Edits the blocks in place, so a list that shares them (the turn's own
    messages) sees the same history.
    """
    return _replace_screenshots(_screenshot_sites(messages)[:-1], OLDER_SCREENSHOT_PLACEHOLDER)


def drop_refused_screenshots(messages: list[dict]) -> int:
    """Replace every render screenshot in `messages` with a note that the
    model refused it; return how many went.

    For a model that takes no image input. The caption and the findings
    beside each screenshot stay, and the note tells the agent why later
    checks come without one. Edits in place, like `drop_older_screenshots`.
    """
    return _replace_screenshots(_screenshot_sites(messages), REFUSED_SCREENSHOT_PLACEHOLDER)


def screenshots_refused(messages: list[dict]) -> bool:
    """Whether `messages` hold a render screenshot the model refused earlier.

    A host that builds a new session for every turn (cowork-server) seeds it
    with the conversation's earlier tool results as
    `anton.cloud_turn.history_rows.split_turn_into_rows` cut them, which keeps
    their text blocks, the note `drop_refused_screenshots` left included. So
    the new session learns from them that the model takes no image input.
    """
    return any(
        isinstance(b, dict) and b.get("type") == "text" and b.get("text") == REFUSED_SCREENSHOT_PLACEHOLDER
        for block in _render_results(messages)
        for b in block["content"]
    )


def _status_reason(status: int) -> str:
    if status == 503:
        return "the render-check service is busy or not ready (HTTP 503)"
    if status == 413:
        return "the page is over the render check's size limit (HTTP 413)"
    if status == 400:
        return "the render-check service rejected the request (HTTP 400)"
    return f"the render-check service answered HTTP {status}"
