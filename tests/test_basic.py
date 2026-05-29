"""Smoke tests for xaxiu-swarm. Avoid network/subprocess by stubbing backends."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from xaxiu_swarm import Backend, DispatchResult, dispatch, swarm
from xaxiu_swarm.state import init_state, new_run_id, read_state, update_worker


class StubBackend(Backend):
    """Records the prompts/packet_paths it receives; returns a canned response."""

    name = "stub"
    default_model = "stub-1"

    def __init__(self, response_text: str = "ok", delay: float = 0.0,
                 status: str = "completed") -> None:
        self.response_text = response_text
        self.delay = delay
        self.status = status
        self.calls: list[dict] = []

    async def dispatch_async(self, prompt, *, packet_path=None, timeout=1800,
                             cwd=None, env=None, model=None, max_iterations=20,
                             add_dirs=None, context_files=None, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append({
            "prompt": prompt,
            "packet_path": str(packet_path) if packet_path else None,
            "context_files": [str(p) for p in (context_files or [])],
        })
        return DispatchResult(
            status=self.status,
            backend=self.name,
            model=model or self.default_model,
            prompt=prompt,
            response=self.response_text,
            elapsed_s=self.delay,
            packet_path=str(packet_path) if packet_path else None,
            context_files=[str(p) for p in (context_files or [])],
        )


def test_dispatch_with_raw_prompt(tmp_path):
    backend = StubBackend(response_text="hello")
    res = dispatch("just a prompt string", backend=backend,
                   audit_dir=tmp_path / "audit", is_packet=False)
    assert res.ok
    assert res.response == "hello"
    assert res.audit_log_path is not None
    assert backend.calls[0]["prompt"] == "just a prompt string"


def test_dispatch_with_packet_path(tmp_path):
    pkt = tmp_path / "pkt.md"
    pkt.write_text("# packet content", encoding="utf-8")
    backend = StubBackend(response_text="done")
    res = dispatch(str(pkt), backend=backend, audit_dir=tmp_path / "audit")
    assert res.ok
    assert backend.calls[0]["packet_path"] == str(pkt)


def test_swarm_parallel(tmp_path):
    backend = StubBackend(response_text="x", delay=0.1)
    pkts = [str(tmp_path / f"p{i}.md") for i in range(3)]
    for p in pkts:
        Path(p).write_text("hi", encoding="utf-8")
    results = asyncio.run(swarm(
        packets=pkts,
        backend=backend,
        max_concurrent=3,
        state_dir=tmp_path / "state",
    ))
    assert len(results) == 3
    assert all(r.ok for r in results)
    assert len(backend.calls) == 3


def test_swarm_mixed_backends(tmp_path):
    a = StubBackend(response_text="A")
    b = StubBackend(response_text="B")
    c = StubBackend(response_text="C")
    pkts = [str(tmp_path / f"p{i}.md") for i in range(3)]
    for p in pkts:
        Path(p).write_text("hi", encoding="utf-8")
    results = asyncio.run(swarm(
        packets=pkts,
        backend=[a, b, c],
        state_dir=tmp_path / "state",
    ))
    assert results[0].response == "A"
    assert results[1].response == "B"
    assert results[2].response == "C"


def test_state_atomic_update(tmp_path):
    sp = tmp_path / "swarm.json"
    rid = new_run_id()
    workers = [
        {"id": "worker-1", "backend": "stub", "packet": "p", "status": "pending"},
        {"id": "worker-2", "backend": "stub", "packet": "p", "status": "pending"},
    ]
    init_state(sp, rid, workers)
    update_worker(sp, "worker-1", status="running")
    update_worker(sp, "worker-1", status="completed", elapsed_s=1.5)
    s = read_state(sp)
    assert s["summary"]["completed"] == 1
    assert s["summary"]["pending"] == 1


def test_swarm_failure_isolation(tmp_path):
    """A failing backend doesn't take down peers; failures returned as failed status."""
    good = StubBackend(response_text="ok")
    bad = StubBackend(status="failed", response_text="")
    pkts = [str(tmp_path / f"p{i}.md") for i in range(2)]
    for p in pkts:
        Path(p).write_text("hi", encoding="utf-8")
    results = asyncio.run(swarm(
        packets=pkts,
        backend=[good, bad],
        state_dir=tmp_path / "state",
    ))
    assert results[0].ok
    assert not results[1].ok


# ----- v0.2 G16/G17 tests -----


def test_g16_context_files_recorded_in_result(tmp_path):
    """G16: context_files paths should be recorded on DispatchResult."""
    backend = StubBackend(response_text="ok")
    ctx_a = tmp_path / "a.txt"
    ctx_a.write_text("file a content", encoding="utf-8")
    ctx_b = tmp_path / "b.txt"
    ctx_b.write_text("file b content", encoding="utf-8")
    res = dispatch(
        "irrelevant prompt",
        backend=backend,
        is_packet=False,
        context_files=[ctx_a, ctx_b],
    )
    assert res.ok
    assert ctx_a.name in str(res.context_files) or len(res.context_files) == 2


def test_g16_format_context_files_helper():
    """G16: _format_context_files produces well-formed inlined block."""
    from xaxiu_swarm.backends.base import Backend
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "v_file.html"
        f.write_text("<!doctype html>\n<html>\n</html>", encoding="utf-8")
        block = Backend._format_context_files([f])
        assert "BEGIN FILE" in block
        assert "END FILE" in block
        assert "<!doctype html>" in block

    # Empty list returns empty string
    assert Backend._format_context_files(None) == ""
    assert Backend._format_context_files([]) == ""


def test_g17_extract_deliverable_path_write_to():
    """G17: 'Write report to <path>' pattern is extracted."""
    from xaxiu_swarm.dispatch import extract_deliverable_path

    text = "## DELIVERABLE\n\nWrite report to `D:\\Projects\\foo\\bar.md`. WRITE BEFORE EXITING."
    p = extract_deliverable_path(text)
    assert p is not None
    assert str(p).endswith("bar.md")


def test_g17_extract_deliverable_path_notice_form():
    """G17: 'DELIVERABLE NOTICE: `<path>`' pattern is extracted."""
    from xaxiu_swarm.dispatch import extract_deliverable_path

    text = "## DELIVERABLE NOTICE (PD#26)\n\n`D:\\Projects\\warehouse\\Kimi Download\\report.md`. WRITE BEFORE EXITING."
    p = extract_deliverable_path(text)
    assert p is not None
    assert str(p).endswith("report.md")


def test_g17_extract_deliverable_path_no_match():
    """G17: returns None when no pattern matches."""
    from xaxiu_swarm.dispatch import extract_deliverable_path

    assert extract_deliverable_path("just plain text") is None
    assert extract_deliverable_path("write something somewhere") is None


def test_g17_auto_write_deliverable_for_api_backend(tmp_path):
    """G17: when API backend completes successfully, response is auto-written to packet's deliverable path."""

    class StubAPIBackend(StubBackend):
        name = "deepseek"  # alias as API-class for the auto-write rule

    backend = StubAPIBackend(response_text="# Report Content\nVerdict: CLEAN")
    pkt = tmp_path / "pkt.md"
    deliverable = tmp_path / "out" / "report.md"
    pkt.write_text(
        f"## DELIVERABLE NOTICE\n\nWrite report to `{deliverable}`. WRITE BEFORE EXITING.",
        encoding="utf-8",
    )
    res = dispatch(str(pkt), backend=backend, audit_dir=tmp_path / "audit")
    assert res.ok
    assert res.deliverable_path is not None
    assert deliverable.exists()
    content = deliverable.read_text(encoding="utf-8")
    assert content.startswith("# Report Content")


def test_g17_auto_write_skipped_for_subprocess_backend(tmp_path):
    """G17: kimi backend is NOT auto-written (subprocess writes its own files)."""

    class KimiAliasBackend(StubBackend):
        name = "kimi"

    backend = KimiAliasBackend(response_text="kimi self-wrote elsewhere")
    pkt = tmp_path / "pkt.md"
    deliverable = tmp_path / "out" / "report.md"
    pkt.write_text(
        f"Write report to `{deliverable}`. WRITE BEFORE EXITING.",
        encoding="utf-8",
    )
    res = dispatch(str(pkt), backend=backend, audit_dir=tmp_path / "audit")
    assert res.ok
    # Kimi alias is in API_BACKENDS check → name = "kimi" is NOT in API_BACKENDS,
    # so auto-write should be skipped
    assert res.deliverable_path is None
    assert not deliverable.exists()


def test_g17_explicit_deliverable_overrides_extraction(tmp_path):
    """G17: explicit deliverable_path overrides packet-text extraction."""

    class StubAPIBackend(StubBackend):
        name = "deepseek"

    backend = StubAPIBackend(response_text="content")
    pkt = tmp_path / "pkt.md"
    pkt.write_text("Write report to `someother.md`.", encoding="utf-8")
    explicit = tmp_path / "explicit.md"
    res = dispatch(
        str(pkt),
        backend=backend,
        deliverable_path=explicit,
        audit_dir=tmp_path / "audit",
    )
    assert res.ok
    assert explicit.exists()
    assert res.deliverable_path == str(explicit.resolve())


def test_g17_disable_auto_deliverable(tmp_path):
    """G17: auto_deliverable=False suppresses auto-write."""

    class StubAPIBackend(StubBackend):
        name = "deepseek"

    backend = StubAPIBackend(response_text="content")
    pkt = tmp_path / "pkt.md"
    deliverable = tmp_path / "out.md"
    pkt.write_text(f"Write report to `{deliverable}`.", encoding="utf-8")
    res = dispatch(
        str(pkt),
        backend=backend,
        audit_dir=tmp_path / "audit",
        auto_deliverable=False,
    )
    assert res.ok
    assert res.deliverable_path is None
    assert not deliverable.exists()


# ----- v0.2.2 G18/G22 tests -----


def test_g18_audit_default_max_len_50000(tmp_path):
    """G18: default audit truncation cap is 50000 chars (was 5000 in v0.1.0)."""
    import json
    from xaxiu_swarm.audit import DEFAULT_AUDIT_MAX_LEN

    assert DEFAULT_AUDIT_MAX_LEN == 50_000

    backend = StubBackend(response_text="x" * 30_000)  # 30KB response, under 50KB default
    res = dispatch("prompt", backend=backend, is_packet=False, audit_dir=tmp_path / "audit")
    assert res.ok
    audit_path = res.audit_log_path
    assert audit_path is not None
    audit = json.loads(open(audit_path, encoding="utf-8").read())
    # Response under default cap → preserved verbatim
    assert audit["response"] == "x" * 30_000
    assert "[truncated" not in audit["response"]


def test_g18_audit_max_len_truncates(tmp_path):
    """G18: explicit small max_len truncates as expected."""
    import json

    backend = StubBackend(response_text="y" * 1000)
    res = dispatch(
        "prompt",
        backend=backend,
        is_packet=False,
        audit_dir=tmp_path / "audit",
        audit_max_len=200,
    )
    assert res.ok
    audit = json.loads(open(res.audit_log_path, encoding="utf-8").read())
    assert "[truncated" in audit["response"]
    assert len(audit["response"]) < 1000


def test_g18_audit_max_len_zero_disables_truncation(tmp_path):
    """G18: max_len=0 disables truncation entirely."""
    import json

    huge = "z" * 100_000  # 100KB
    backend = StubBackend(response_text=huge)
    res = dispatch(
        "prompt",
        backend=backend,
        is_packet=False,
        audit_dir=tmp_path / "audit",
        audit_max_len=0,
    )
    assert res.ok
    audit = json.loads(open(res.audit_log_path, encoding="utf-8").read())
    assert audit["response"] == huge
    assert "[truncated" not in audit["response"]


def test_g22_heartbeat_updates_swarm_json(tmp_path):
    """G22: heartbeat_interval_s updates swarm.json's last_heartbeat field while worker runs."""
    import json
    import time

    # Backend with a real delay so the heartbeat has time to fire
    backend = StubBackend(response_text="ok", delay=0.3)

    pkts = [str(tmp_path / "p.md")]
    Path(pkts[0]).write_text("hi", encoding="utf-8")

    state_dir = tmp_path / "state"

    async def _run():
        return await swarm(
            packets=pkts,
            backend=backend,
            state_dir=state_dir,
            heartbeat_interval_s=0.05,  # 50ms — beats fast for test speed
        )

    results = asyncio.run(_run())
    assert results[0].ok
    # Find the run dir
    run_dirs = [p for p in state_dir.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    swarm_json = run_dirs[0] / "swarm.json"
    state = json.loads(swarm_json.read_text(encoding="utf-8"))
    # Worker should have last_heartbeat set (because the worker ran 0.3s and
    # heartbeat interval was 0.05s → at least a few beats fired)
    worker = state["workers"][0]
    assert "last_heartbeat" in worker, f"worker fields: {list(worker)}"
    assert worker["last_heartbeat"] is not None


def test_g22_heartbeat_disabled_with_zero_interval(tmp_path):
    """G22: heartbeat_interval_s=0 disables the heartbeat task."""
    import json

    backend = StubBackend(response_text="ok", delay=0.1)
    pkts = [str(tmp_path / "p.md")]
    Path(pkts[0]).write_text("hi", encoding="utf-8")
    state_dir = tmp_path / "state"

    async def _run():
        return await swarm(
            packets=pkts,
            backend=backend,
            state_dir=state_dir,
            heartbeat_interval_s=0,
        )

    asyncio.run(_run())
    run_dirs = [p for p in state_dir.iterdir() if p.is_dir()]
    state = json.loads((run_dirs[0] / "swarm.json").read_text(encoding="utf-8"))
    worker = state["workers"][0]
    # last_heartbeat may be unset (None) since heartbeat task never ran past sleep(0)
    assert worker.get("last_heartbeat") is None


# ----- v0.2.3 G26/G30 tests -----


def test_g26_extract_deliverable_returns_unresolved(tmp_path):
    """G26: extract_deliverable_path returns Path AS-IS (not pre-resolved)."""
    from xaxiu_swarm.dispatch import extract_deliverable_path

    text = "Write report to `relative/output.md`."
    p = extract_deliverable_path(text)
    assert p is not None
    # NOT pre-resolved → should be relative
    assert not p.is_absolute()
    assert str(p) == "relative\\output.md" or str(p) == "relative/output.md"


def test_g26_resolve_deliverable_relative_with_cwd(tmp_path):
    """G26: relative path resolves against provided worker cwd, not process cwd."""
    from xaxiu_swarm.dispatch import _resolve_deliverable

    rel = Path("output.md")
    worker_cwd = tmp_path / "fake_worktree"
    worker_cwd.mkdir(parents=True, exist_ok=True)
    resolved = _resolve_deliverable(rel, worker_cwd)
    assert resolved == (worker_cwd / "output.md").resolve()


def test_g26_resolve_deliverable_absolute_passes_through(tmp_path):
    """G26: absolute paths are unaffected by worker cwd."""
    from xaxiu_swarm.dispatch import _resolve_deliverable

    abs_path = (tmp_path / "absolute_target.md").resolve()
    worker_cwd = tmp_path / "fake_worktree"
    worker_cwd.mkdir(parents=True, exist_ok=True)
    resolved = _resolve_deliverable(abs_path, worker_cwd)
    assert resolved == abs_path


def test_g26_auto_write_uses_worker_cwd_for_relative_path(tmp_path):
    """G26: full integration — DeepSeek auto-write resolves relative deliverable
    against the cwd kwarg (passed through kwargs to backend.dispatch_async)."""

    class StubAPIBackend(StubBackend):
        name = "deepseek"

    backend = StubAPIBackend(response_text="cwd-aware content")
    pkt = tmp_path / "pkt.md"
    worker_cwd = tmp_path / "worker1_worktree"
    worker_cwd.mkdir(parents=True)
    pkt.write_text("Write report to `cwd_aware_output.md`.", encoding="utf-8")

    res = dispatch(
        str(pkt),
        backend=backend,
        cwd=worker_cwd,
        audit_dir=tmp_path / "audit",
    )
    assert res.ok
    expected = (worker_cwd / "cwd_aware_output.md").resolve()
    assert res.deliverable_path == str(expected)
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == "cwd-aware content"


def test_g30_progress_disabled_by_default(tmp_path, capsys):
    """G30: progress_interval_s=0 (default in dispatch_async) emits no stderr pings."""
    backend = StubBackend(response_text="quick")
    res = dispatch("prompt", backend=backend, is_packet=False, audit_dir=tmp_path / "audit")
    captured = capsys.readouterr()
    assert "still running" not in captured.err
    assert res.ok


def test_g30_progress_pings_stderr(tmp_path, capsys):
    """G30: progress_interval_s>0 emits stderr heartbeats during a delayed worker."""
    backend = StubBackend(response_text="ok", delay=0.25)  # 250ms delay
    res = dispatch(
        "prompt",
        backend=backend,
        is_packet=False,
        audit_dir=tmp_path / "audit",
        progress_interval_s=0.05,  # 50ms beats
    )
    captured = capsys.readouterr()
    # During 250ms wait with 50ms beats, expect ~3-4 pings
    assert "still running" in captured.err
    assert "xaxiu-swarm/stub" in captured.err
    assert res.ok


# ----- v0.2.4 G31 tests -----


def test_g31_deepseek_backend_reads_env_model(monkeypatch):
    """G31: DeepSeekBackend honors DEEPSEEK_MODEL env var when no explicit model arg."""
    from xaxiu_swarm.backends.deepseek import DeepSeekBackend

    monkeypatch.setenv("DEEPSEEK_API_KEY", "stub-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    b = DeepSeekBackend()
    assert b.default_model == "deepseek-v4-pro"


def test_g31_deepseek_backend_explicit_model_overrides_env(monkeypatch):
    """G31: explicit model= arg overrides DEEPSEEK_MODEL env."""
    from xaxiu_swarm.backends.deepseek import DeepSeekBackend

    monkeypatch.setenv("DEEPSEEK_API_KEY", "stub-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    b = DeepSeekBackend(model="deepseek-reasoner")
    assert b.default_model == "deepseek-reasoner"


def test_g31_deepseek_backend_falls_back_to_class_default(monkeypatch):
    """G31: when no env, no explicit, fall through to class default."""
    from xaxiu_swarm.backends.deepseek import DeepSeekBackend

    monkeypatch.setenv("DEEPSEEK_API_KEY", "stub-key")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    b = DeepSeekBackend()
    assert b.default_model == "deepseek-chat"


def test_g31_qwen_backend_reads_env_model(monkeypatch):
    """G31 parity: QwenBackend honors QWEN_MODEL env var."""
    from xaxiu_swarm.backends.qwen import QwenBackend

    monkeypatch.setenv("QWEN_API_KEY", "stub-key")
    monkeypatch.setenv("QWEN_MODEL", "qwen/qwen3-235b-coder")
    b = QwenBackend()
    assert b.default_model == "qwen/qwen3-235b-coder"


def test_g31_claude_backend_reads_env_model(monkeypatch):
    """G31 parity: ClaudeBackend honors ANTHROPIC_MODEL env var."""
    from xaxiu_swarm.backends.claude import ClaudeBackend

    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    b = ClaudeBackend()
    assert b.default_model == "claude-opus-4-7"


# ----- v0.3.0 G32 tests -----


def test_g32_build_ag1_prompt_includes_verification(tmp_path):
    """G32: built prompt includes the source-trace verification clause by default."""
    from xaxiu_swarm.ag1 import build_ag1_prompt, DEFAULT_VERIFICATION_CLAUSE

    p = build_ag1_prompt(
        subject="V_CONV4g Wave 43 hotfix",
        cohort_reports=[tmp_path / "engineer.md"],
        base_question="Verdict CLEAN/YELLOW/RED?",
    )
    assert "V_CONV4g" in p
    assert "engineer.md" in p
    assert "Source-trace verification" in p
    # The exact verification language must be present
    assert "FALSE POSITIVE" in p
    assert "useCallback" in p  # mentions React stale-closure example


def test_g32_build_ag1_prompt_can_disable_verification():
    """G32: passing empty verification_clause disables the source-trace requirement."""
    from xaxiu_swarm.ag1 import build_ag1_prompt

    p = build_ag1_prompt(
        subject="trivial",
        base_question="Q?",
        verification_clause="",
    )
    assert "Source-trace verification" not in p
    assert "FALSE POSITIVE" not in p


def test_g32_ag1_meta_review_calls_dispatch_with_context(tmp_path):
    """G32: ag1_meta_review forwards cohort_reports + source_files as context_files."""
    import asyncio
    from xaxiu_swarm.ag1 import ag1_meta_review

    class CapturingBackend(StubBackend):
        name = "deepseek"  # treated as API backend for G17 path
        captured: dict = {}

        async def dispatch_async(self, prompt, *, packet_path=None, timeout=1800,
                                 cwd=None, env=None, model=None, max_iterations=20,
                                 add_dirs=None, context_files=None, **kwargs):
            CapturingBackend.captured["prompt"] = prompt
            CapturingBackend.captured["context_files"] = [str(p) for p in (context_files or [])]
            return await super().dispatch_async(
                prompt, packet_path=packet_path, timeout=timeout,
                cwd=cwd, env=env, model=model, max_iterations=max_iterations,
                add_dirs=add_dirs, context_files=context_files, **kwargs
            )

    backend = CapturingBackend(response_text="**Verdict: CLEAN**")
    r1 = tmp_path / "engineer.md"; r1.write_text("Engineer report", encoding="utf-8")
    r2 = tmp_path / "di.md"; r2.write_text("DI report", encoding="utf-8")
    src = tmp_path / "V_thing.html"; src.write_text("source code", encoding="utf-8")
    deliv = tmp_path / "ag1_out.md"

    res = asyncio.run(ag1_meta_review(
        subject="bench wave",
        cohort_reports=[r1, r2],
        source_files=[src],
        backend=backend,
        deliverable_path=deliv,
        audit_dir=tmp_path / "audit",
    ))
    assert res.ok
    # Prompt should include G32 verification clause
    assert "Source-trace verification" in CapturingBackend.captured["prompt"]
    # Context files = cohort_reports + source_files
    cf = CapturingBackend.captured["context_files"]
    assert any("engineer.md" in c for c in cf)
    assert any("di.md" in c for c in cf)
    assert any("V_thing.html" in c for c in cf)
    # Deliverable auto-written via G17
    assert deliv.exists()
    assert "Verdict: CLEAN" in deliv.read_text(encoding="utf-8")


def test_g32_ag1_meta_review_no_source_trace_clause(tmp_path):
    """G32: passing empty verification_clause skips the source-trace clause in the prompt."""
    import asyncio
    from xaxiu_swarm.ag1 import ag1_meta_review

    class CapturingBackend(StubBackend):
        name = "deepseek"
        captured: dict = {}

        async def dispatch_async(self, prompt, *, packet_path=None, timeout=1800,
                                 cwd=None, env=None, model=None, max_iterations=20,
                                 add_dirs=None, context_files=None, **kwargs):
            CapturingBackend.captured["prompt"] = prompt
            return await super().dispatch_async(
                prompt, packet_path=packet_path, timeout=timeout,
                cwd=cwd, env=env, model=model, max_iterations=max_iterations,
                add_dirs=add_dirs, context_files=context_files, **kwargs
            )

    backend = CapturingBackend(response_text="ok")
    r = tmp_path / "r.md"; r.write_text("x", encoding="utf-8")

    asyncio.run(ag1_meta_review(
        subject="bench",
        cohort_reports=[r],
        backend=backend,
        deliverable_path=tmp_path / "out.md",
        verification_clause="",
        audit_dir=tmp_path / "audit",
    ))
    assert "Source-trace verification" not in CapturingBackend.captured["prompt"]


# ----- v0.4.0 OpenCode / MiMo backend tests -----


def test_opencode_registered():
    """OpenCode is in the registry under 'opencode' and the 'mimo' alias."""
    from xaxiu_swarm.backends import list_backends, get_backend

    assert "opencode" in list_backends()
    assert "mimo" in list_backends()
    assert get_backend("opencode").name == "opencode"
    assert get_backend("mimo").name == "opencode"  # alias resolves to same backend


def test_opencode_not_in_api_backends():
    """OpenCode is filesystem-capable (self-writes via tools), so it must NOT be
    in API_BACKENDS — dispatch() should never auto-write its stdout."""
    from xaxiu_swarm.dispatch import API_BACKENDS

    assert "opencode" not in API_BACKENDS


def test_opencode_default_model(monkeypatch, tmp_path):
    """Default model is the qualified mimo/mimo-v2.5-pro."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.delenv("MIMO_MODEL", raising=False)
    be = OpenCodeBackend(config_path=tmp_path / "oc.json")
    assert be.default_model == "mimo/mimo-v2.5-pro"


def test_opencode_reads_env_model(monkeypatch, tmp_path):
    """MIMO_MODEL env is honored and auto-qualified with the provider prefix."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.setenv("MIMO_MODEL", "mimo-v2.5")
    be = OpenCodeBackend(config_path=tmp_path / "oc.json")
    assert be.default_model == "mimo/mimo-v2.5"


def test_opencode_explicit_model_overrides_env(monkeypatch, tmp_path):
    """Explicit model arg beats MIMO_MODEL env."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.setenv("MIMO_MODEL", "mimo-v2.5")
    be = OpenCodeBackend(model="mimo-v2-flash", config_path=tmp_path / "oc.json")
    assert be.default_model == "mimo/mimo-v2-flash"


def test_opencode_qualify_model_passthrough(tmp_path):
    """A fully-qualified provider/model is used as-is; a bare id gets the prefix."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    be = OpenCodeBackend(config_path=tmp_path / "oc.json")
    assert be._qualify_model("mimo-v2.5-pro") == "mimo/mimo-v2.5-pro"
    assert be._qualify_model("anthropic/claude-sonnet") == "anthropic/claude-sonnet"


def test_opencode_base_url_env(monkeypatch, tmp_path):
    """MIMO_BASE_URL env overrides the default endpoint (e.g. Token-Plan CN)."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.setenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
    be = OpenCodeBackend(config_path=tmp_path / "oc.json")
    assert be.base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert be._config_dict()["provider"]["mimo"]["options"]["baseURL"] == (
        "https://token-plan-cn.xiaomimimo.com/v1"
    )


def test_opencode_api_key_selection(monkeypatch):
    """Key precedence: explicit > MIMO_API_KEYS pool > MIMO_API_KEY single."""
    from xaxiu_swarm.backends.opencode import _select_api_key

    monkeypatch.delenv("MIMO_API_KEYS", raising=False)
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    assert _select_api_key(None) is None
    assert _select_api_key("explicit-key") == "explicit-key"

    monkeypatch.setenv("MIMO_API_KEY", "single-key")
    assert _select_api_key(None) == "single-key"
    assert _select_api_key("explicit-key") == "explicit-key"  # explicit still wins

    monkeypatch.setenv("MIMO_API_KEYS", "k1,k2,k3")
    for _ in range(20):
        assert _select_api_key(None) in {"k1", "k2", "k3", "single-key"}


def test_opencode_config_dict_uses_env_substitution(tmp_path):
    """Generated config references the key via {env:MIMO_API_KEY} — no secret on disk."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    be = OpenCodeBackend(config_path=tmp_path / "oc.json")
    cfg = be._config_dict()
    mimo = cfg["provider"]["mimo"]
    assert mimo["npm"] == "@ai-sdk/openai-compatible"
    assert mimo["options"]["apiKey"] == "{env:MIMO_API_KEY}"
    assert "mimo-v2.5-pro" in mimo["models"]


def test_opencode_generates_managed_config(monkeypatch):
    """With no explicit config_path / OPENCODE_CONFIG, a managed config is written."""
    import json
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    be = OpenCodeBackend()
    assert be._own_config is True
    assert be.config_path.exists()
    cfg = json.loads(be.config_path.read_text(encoding="utf-8"))
    assert "mimo" in cfg["provider"]


def test_opencode_honors_env_config(monkeypatch, tmp_path):
    """OPENCODE_CONFIG env points the backend at a user-managed config (no generation)."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    user_cfg = tmp_path / "my-opencode.json"
    monkeypatch.setenv("OPENCODE_CONFIG", str(user_cfg))
    be = OpenCodeBackend()
    assert be._own_config is False
    assert be.config_path == user_cfg
    assert not user_cfg.exists()  # we did NOT write it


def test_opencode_build_command(tmp_path):
    """Command includes run, -m model, --format json, skip-permissions, message last."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    be = OpenCodeBackend(config_path=tmp_path / "oc.json", skip_permissions=True)
    cmd = be._build_command("THE MESSAGE", "mimo/mimo-v2.5-pro", [])
    assert cmd[1] == "run"
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "mimo/mimo-v2.5-pro"
    assert "--format" in cmd and cmd[cmd.index("--format") + 1] == "json"
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[-1] == "THE MESSAGE"


def test_opencode_build_command_no_skip_permissions(tmp_path):
    """skip_permissions=False omits the dangerous flag."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    be = OpenCodeBackend(config_path=tmp_path / "oc.json", skip_permissions=False)
    cmd = be._build_command("msg", "mimo/mimo-v2.5-pro", [])
    assert "--dangerously-skip-permissions" not in cmd


def test_opencode_build_command_attaches_files_after_message(tmp_path):
    """Attached files (images) are passed via repeated -f AFTER the message — the
    -f array option would otherwise swallow a trailing positional message."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    be = OpenCodeBackend(config_path=tmp_path / "oc.json")
    files = [tmp_path / "a.png", tmp_path / "b.png"]
    cmd = be._build_command("THE MESSAGE", "mimo/mimo-v2.5-pro", files)
    msg_idx = cmd.index("THE MESSAGE")
    f_idxs = [i for i, t in enumerate(cmd) if t == "-f"]
    assert len(f_idxs) == 2
    assert msg_idx < min(f_idxs), "message must come before any -f flag"
    attached = {cmd[i + 1] for i in f_idxs}
    assert str(files[0]) in attached and str(files[1]) in attached


def test_opencode_message_references_packet_by_path(tmp_path):
    """Packet is cited by absolute path in the message (NOT via -f, whose array
    option would swallow the trailing positional message)."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    pkt = tmp_path / "packet.md"
    msg = OpenCodeBackend._build_message("", pkt, [])
    assert str(pkt) in msg
    assert "authoritative work order" in msg
    # The image-only attach list means the command never -f's the packet.
    be = OpenCodeBackend(config_path=tmp_path / "oc.json")
    cmd = be._build_command(msg, "mimo/mimo-v2.5-pro", [])  # no images
    assert "-f" not in cmd
    assert cmd[-1] == msg


def test_opencode_message_references_context_files(tmp_path):
    """Context files are cited by path for both packet and raw-prompt dispatches."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    ctx = [tmp_path / "v_src.html", tmp_path / "spec.md"]
    # raw prompt + context
    m1 = OpenCodeBackend._build_message("do the thing", None, ctx)
    assert "do the thing" in m1
    assert all(str(c) in m1 for c in ctx)
    # packet + context
    m2 = OpenCodeBackend._build_message("", tmp_path / "p.md", ctx)
    assert all(str(c) in m2 for c in ctx)


def test_opencode_skip_permissions_env(monkeypatch, tmp_path):
    """OPENCODE_SKIP_PERMISSIONS env: default on; '0'/'false' turns it off."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.delenv("OPENCODE_SKIP_PERMISSIONS", raising=False)
    assert OpenCodeBackend(config_path=tmp_path / "a.json").skip_permissions is True

    monkeypatch.setenv("OPENCODE_SKIP_PERMISSIONS", "0")
    assert OpenCodeBackend(config_path=tmp_path / "b.json").skip_permissions is False

    monkeypatch.setenv("OPENCODE_SKIP_PERMISSIONS", "false")
    assert OpenCodeBackend(config_path=tmp_path / "c.json").skip_permissions is False


def test_opencode_extract_json_text():
    """JSON event stream → concatenated assistant text; non-JSON noise ignored."""
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    stream = (
        "Performing one time database migration...\n"   # non-JSON banner, ignored
        '{"type":"text","text":"Hello "}\n'
        '{"type":"step-start"}\n'
        '{"part":{"type":"text","text":"world"}}\n'      # nested text part
        '{"type":"tool","input":{"text":"DO NOT CAPTURE THIS"}}\n'  # not a text part
    )
    text, err = OpenCodeBackend._extract_json(stream)
    assert err is None
    assert text == "Hello world"
    assert "DO NOT CAPTURE" not in text


def test_opencode_extract_json_error_event():
    """A {"type":"error",...} event is surfaced as the error message, response empty.

    Uses the real shape observed from opencode 1.x (APIError / data.message / statusCode).
    """
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    stream = (
        '{"type":"error","error":{"name":"APIError",'
        '"data":{"message":"Forbidden: Host not in allowlist","statusCode":403}}}'
    )
    text, err = OpenCodeBackend._extract_json(stream)
    assert text == ""  # error JSON is not echoed as a response
    assert err is not None
    assert "Forbidden" in err and "403" in err


def test_opencode_missing_key_fails_without_spawn(monkeypatch, tmp_path):
    """No MIMO key → failed DispatchResult, returned before any subprocess spawn."""
    import asyncio
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("MIMO_API_KEYS", raising=False)
    # Point at a non-existent binary to prove we never try to run it.
    be = OpenCodeBackend(
        config_path=tmp_path / "oc.json",
        opencode_path=str(tmp_path / "no-such-opencode"),
    )
    res = asyncio.run(be.dispatch_async("hi", timeout=5))
    assert res.status == "failed"
    assert "MIMO_API_KEY not set" in (res.error or "")


def test_opencode_binary_not_found_fails_cleanly(monkeypatch, tmp_path):
    """Missing opencode binary → failed result with an install hint (never raises)."""
    import asyncio
    from xaxiu_swarm.backends.opencode import OpenCodeBackend

    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    be = OpenCodeBackend(
        config_path=tmp_path / "oc.json",
        opencode_path=str(tmp_path / "definitely-not-here"),
    )
    res = asyncio.run(be.dispatch_async("hi", timeout=5))
    assert res.status == "failed"
    assert "opencode binary not found" in (res.error or "")
    assert "install" in (res.error or "").lower()
