"""OpenCode CLI subprocess backend — drives the Xiaomi MiMo API by default.

opencode (https://opencode.ai) is a terminal AI coding agent. This backend
invokes it in non-interactive ``run`` mode::

    opencode run -m <provider/model> --print-logs --log-level ERROR \
        [--dangerously-skip-permissions] <message> [-f <image> ...]

and captures stdout as the response. Like the Kimi CLI backend it is a
*filesystem-capable agent*: the dispatch packet and any context files are passed
by absolute path in the message (opencode reads them via its own tools), and when
the packet instructs it to write a deliverable, opencode does so via its
write/edit/bash tools. It is therefore NOT in ``dispatch.API_BACKENDS`` — there is
no auto-write of stdout to a deliverable path (opencode self-writes per packet
instructions, exactly like the Kimi backend).

MiMo wiring
-----------
The Xiaomi MiMo API is OpenAI-compatible at ``https://api.xiaomimimo.com/v1``.
opencode reaches it through a custom ``@ai-sdk/openai-compatible`` provider
defined in an opencode config file. This backend GENERATES that config
automatically (unless you point it at your own via ``OPENCODE_CONFIG`` or
``config_path=``), so the only thing a caller must supply is the API key::

    export MIMO_API_KEY=sk-...          # pay-as-you-go (sk-…) or Token Plan (tp-…) key
    xaxiu-swarm dispatch packet.md --backend opencode

The generated config stores ``"apiKey": "{env:MIMO_API_KEY}"`` (opencode's env
substitution) rather than a literal secret, and this backend sets
``MIMO_API_KEY`` in the subprocess environment to the per-dispatch key selected
from the pool — so multi-key sharding (``MIMO_API_KEYS``) works the same way it
does for the DeepSeek / Kimi-API backends.

Env vars
--------
    MIMO_API_KEY      single key (back-compat).
    MIMO_API_KEYS     CSV pool of keys; one is chosen per dispatch (sharding
                      parity with the deepseek / kimi-api backends).
    MIMO_BASE_URL     OpenAI-compatible base URL (default
                      ``https://api.xiaomimimo.com/v1``; Token-Plan CN users:
                      ``https://token-plan-cn.xiaomimimo.com/v1``).
    MIMO_MODEL        model id (default ``mimo-v2.5-pro``). A bare id such as
                      ``mimo-v2.5`` is auto-qualified to ``mimo/mimo-v2.5``; a
                      fully-qualified ``provider/model`` is used as-is.
    OPENCODE_CONFIG   path to an opencode config you manage yourself. When set,
                      this backend uses it verbatim and does NOT generate one —
                      so configure the ``mimo`` provider there.
    OPENCODE_BIN      path to the opencode binary (else ``shutil.which`` lookup).
    OPENCODE_SKIP_PERMISSIONS  ``0``/``false`` to force interactive permission
                      prompts. Default is to pass ``--dangerously-skip-permissions``
                      (parity with the Kimi backend's implicit ``--yolo``) so
                      opencode can run codemod packets unattended; without it a
                      non-interactive run that hits a permission gate would hang
                      to timeout.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from xaxiu_swarm.backends.base import Backend, DispatchResult


def _select_api_key(passed: str | None) -> str | None:
    """Select an API key for this dispatch.

    Precedence (mirrors the deepseek / kimi-api backends):
      1. Explicit ``passed`` (constructor arg) — wins, no pool consulted.
      2. ``MIMO_API_KEYS`` env (CSV pool) — random.choice across entries.
      3. ``MIMO_API_KEY`` env (single, back-compat) — appended to the pool so it
         is always eligible.

    Returns None if no key is configured anywhere.
    """
    import random
    if passed:
        return passed
    pool_csv = os.environ.get("MIMO_API_KEYS", "")
    pool = [k.strip() for k in pool_csv.split(",") if k.strip()]
    single = os.environ.get("MIMO_API_KEY")
    if single and single not in pool:
        pool.append(single)
    return random.choice(pool) if pool else None


# ANSI escape stripper — opencode disables color when stdout is not a TTY, but we
# strip defensively so the captured response is always clean text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


_DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
_DEFAULT_BARE_MODEL = "mimo-v2.5-pro"


class OpenCodeBackend(Backend):
    name = "opencode"
    default_model = "mimo/mimo-v2.5-pro"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        opencode_path: str | None = None,
        config_path: str | Path | None = None,
        provider_id: str = "mimo",
        provider_name: str = "Xiaomi MiMo",
        skip_permissions: bool | None = None,
        output_format: str = "json",
    ) -> None:
        """
        api_key: explicit key (else selected per-dispatch from MIMO_API_KEYS /
            MIMO_API_KEY at dispatch time).
        base_url: OpenAI-compatible endpoint (else MIMO_BASE_URL env / default).
        model: model id; bare ids are qualified with ``<provider_id>/``. Else
            MIMO_MODEL env, else ``mimo-v2.5-pro``.
        opencode_path: path to the opencode binary. Defaults to OPENCODE_BIN env
            then ``shutil.which("opencode")``.
        config_path: explicit opencode config file to use. When None, OPENCODE_CONFIG
            env is honored if set; otherwise a managed config defining the MiMo
            provider is generated and reused.
        provider_id / provider_name: opencode provider identifier + display name.
        skip_permissions: pass ``--dangerously-skip-permissions``. None → read
            OPENCODE_SKIP_PERMISSIONS env (default True).
        output_format: opencode ``--format`` value. "json" (default) parses the
            JSON event stream for the assistant text AND detects ``error`` events
            — necessary because opencode exits 0 even when the underlying API call
            fails (e.g. a 403), so the exit code alone cannot be trusted. "default"
            captures the human-formatted stdout verbatim (no error-event detection).
        """
        self.opencode_path = (
            opencode_path
            or os.environ.get("OPENCODE_BIN")
            or shutil.which("opencode")
            or "opencode"
        )
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.base_url = base_url or os.environ.get("MIMO_BASE_URL", _DEFAULT_BASE_URL)
        self._explicit_api_key = api_key

        # Model resolution: explicit arg > MIMO_MODEL env > class default. Bare
        # ids are qualified with the provider prefix so opencode's provider/model
        # form is always satisfied.
        chosen = model or os.environ.get("MIMO_MODEL") or _DEFAULT_BARE_MODEL
        self.default_model = self._qualify_model(chosen)

        # Config resolution: explicit arg > OPENCODE_CONFIG env (user-managed) >
        # generated managed config. We only own (auto-generate) the config in the
        # last case; otherwise we use the caller's config verbatim.
        env_cfg = os.environ.get("OPENCODE_CONFIG")
        if config_path is not None:
            self.config_path = Path(config_path)
            self._own_config = False
        elif env_cfg:
            self.config_path = Path(env_cfg)
            self._own_config = False
        else:
            self.config_path = self._write_managed_config()
            self._own_config = True

        if skip_permissions is None:
            env_sp = os.environ.get("OPENCODE_SKIP_PERMISSIONS", "").strip().lower()
            # Default True (parity with Kimi --yolo). Only an explicit off value
            # disables it.
            self.skip_permissions = env_sp not in ("0", "false", "no", "off")
        else:
            self.skip_permissions = bool(skip_permissions)

        self.output_format = output_format

    def _qualify_model(self, model: str) -> str:
        """Ensure a ``provider/model`` form. Bare ids get the provider prefix."""
        return model if "/" in model else f"{self.provider_id}/{model}"

    def _config_dict(self) -> dict[str, Any]:
        """opencode config defining the MiMo (OpenAI-compatible) provider.

        apiKey uses opencode's ``{env:VAR}`` substitution rather than a literal
        secret, so nothing sensitive is written to disk; the key is supplied via
        the subprocess environment at dispatch time.
        """
        return {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                self.provider_id: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": self.provider_name,
                    "options": {
                        "baseURL": self.base_url,
                        "apiKey": "{env:MIMO_API_KEY}",
                    },
                    "models": {
                        "mimo-v2.5-pro": {
                            "name": "MiMo V2.5 Pro",
                            "limit": {"context": 1048576, "output": 131072},
                        },
                        "mimo-v2.5": {"name": "MiMo V2.5"},
                        "mimo-v2-pro": {"name": "MiMo V2 Pro"},
                        "mimo-v2-flash": {"name": "MiMo V2 Flash"},
                    },
                }
            },
        }

    def _write_managed_config(self) -> Path:
        """Write the managed MiMo provider config atomically and return its path.

        Deterministic path under the system temp dir so repeated backend
        instances (swarm spawns one per worker) converge on the same file.
        Written via temp-file + ``os.replace`` so concurrent constructors never
        observe a half-written config.
        """
        cfg_dir = Path(tempfile.gettempdir()) / "xaxiu-swarm" / "opencode"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / f"{self.provider_id}.opencode.json"
        payload = json.dumps(self._config_dict(), indent=2)
        fd, tmp_name = tempfile.mkstemp(dir=str(cfg_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, cfg_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return cfg_path

    @staticmethod
    def _build_message(
        prompt: str,
        packet_path: Path | None,
        ctx_paths: list[Path],
    ) -> str:
        """Build the opencode message: a Kimi-style directive citing the packet +
        context files by absolute path (opencode reads them via its own tools), or
        the raw prompt (optionally prefixed with context-file references)."""
        if packet_path is not None:
            ctx_section = ""
            if ctx_paths:
                ctx_lines = "\n".join(f"  - {p}" for p in ctx_paths)
                ctx_section = (
                    f"Authoritative source-of-truth files (read these first to "
                    f"ground your work):\n{ctx_lines}\n\n"
                )
            return (
                f"{ctx_section}"
                f"Read the dispatch packet at the following absolute path and "
                f"execute the spec within it as the authoritative work order:\n"
                f"  {Path(packet_path)}\n\n"
                f"Honor all deliverable notices, acceptance gates, forbidden "
                f"zones, and execution-sequence steps in the packet. Write any "
                f"deliverables to the paths it specifies before finishing.\n\n"
                f"Begin work."
            )
        if ctx_paths:
            ctx_lines = "\n".join(f"  - {p}" for p in ctx_paths)
            return (
                f"Authoritative source-of-truth files (read these first):\n"
                f"{ctx_lines}\n\n{prompt}"
            )
        return prompt

    def _build_command(
        self,
        message: str,
        model: str,
        attach_files: list[Path],
    ) -> list[str]:
        """Assemble the ``opencode run`` argv.

        The message is the first positional; any ``attach_files`` (images) follow
        as ``-f`` flags placed AFTER it. opencode's ``-f`` is an *array* option, so
        a trailing positional message after ``-f`` gets greedily swallowed as
        another filename — keeping the message before ``-f`` avoids that.
        """
        cmd: list[str] = [
            self.opencode_path,
            "run",
            "-m", model,
            "--print-logs",
            "--log-level", "ERROR",
            "--format", self.output_format,
        ]
        if self.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.append(message)
        for f in attach_files:
            cmd.extend(["-f", str(f)])
        return cmd

    @staticmethod
    def _extract_json(stdout: str) -> tuple[str, str | None]:
        """Parse ``--format json`` output into ``(assistant_text, error_message)``.

        opencode emits a stream of JSON values (one per line). We:
          * record the first ``{"type":"error",...}`` event as an error message —
            opencode exits 0 even on API failures, so this is how we detect them;
          * accumulate text from assistant ``text`` parts;
          * fall back to the raw stdout for the text if nothing parseable is found
            (so a schema change never silently drops a response).

        Non-JSON lines (e.g. the one-time DB-migration banner) are ignored.
        """
        texts: list[str] = []
        error_msg: str | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line or line[0] not in "{[":
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "error":
                if error_msg is None:
                    error_msg = _format_error_event(obj)
                continue
            texts.extend(_collect_text_parts(obj))
        joined = "".join(texts).strip()
        if not joined:
            # Don't echo the raw error JSON as the "response" — leave it empty so
            # the error field carries the signal.
            joined = "" if error_msg else stdout.strip()
        return joined, error_msg

    async def dispatch_async(
        self,
        prompt: str,
        *,
        packet_path: Path | None = None,
        timeout: int = 1800,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        model: str | None = None,
        max_iterations: int = 20,
        add_dirs: list[Path] | None = None,
        context_files: list[Path] | None = None,
        image_paths: list[Path] | None = None,
        **kwargs: Any,
    ) -> DispatchResult:
        chosen_model = self._qualify_model(model) if model else self.default_model

        api_key = _select_api_key(self._explicit_api_key)
        if not api_key:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=chosen_model,
                prompt=prompt,
                response="",
                elapsed_s=0.0,
                error="MIMO_API_KEY not set (Xiaomi MiMo key from platform.xiaomimimo.com)",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in (context_files or [])],
            )

        # opencode is a filesystem-capable agent (read/bash tools); with
        # --dangerously-skip-permissions it can open any path we cite. We pass the
        # packet + context files BY ABSOLUTE PATH in the directive (like the Kimi
        # backend) rather than via -f: that keeps the CLI message small regardless
        # of file size (no OS arg-size limit) and avoids opencode's -f array option
        # greedily swallowing the trailing positional message. Images still ride
        # -f (placed after the message so the array can't consume it).
        ctx_paths = [Path(c).resolve() for c in (context_files or [])]
        attach_files = [Path(p) for p in (image_paths or [])]
        message = self._build_message(prompt, packet_path, ctx_paths)

        cmd = self._build_command(message, chosen_model, attach_files)

        sub_env = os.environ.copy()
        if env:
            sub_env.update(env)
        # Supply the sharded key via the env var the generated config references.
        sub_env["MIMO_API_KEY"] = api_key
        # Point opencode at the (managed or user) config and keep output clean.
        sub_env["OPENCODE_CONFIG"] = str(self.config_path)
        sub_env["NO_COLOR"] = "1"
        sub_env["PYTHONIOENCODING"] = "utf-8"
        sub_env["PYTHONUTF8"] = "1"

        start = self._now()
        proc: asyncio.subprocess.Process | None = None
        stdout_b = b""
        stderr_b = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
                env=sub_env,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    stdout_b, stderr_b = await proc.communicate()
                except Exception:
                    pass
                return DispatchResult(
                    status="timeout",
                    backend=self.name,
                    model=chosen_model,
                    prompt=message,
                    response=_strip_ansi(stdout_b.decode("utf-8", errors="replace")),
                    elapsed_s=self._now() - start,
                    exit_code=proc.returncode,
                    error=f"opencode timeout (>{timeout}s)",
                    packet_path=str(packet_path) if packet_path else None,
                    context_files=[str(p) for p in ctx_paths],
                )
        except FileNotFoundError:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=chosen_model,
                prompt=message,
                response="",
                elapsed_s=self._now() - start,
                error=(
                    f"opencode binary not found at {self.opencode_path!r}. "
                    f"Install it (npm i -g opencode-ai, or "
                    f"`curl -fsSL https://opencode.ai/install | bash`) or run "
                    f"scripts/install_opencode.sh."
                ),
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in ctx_paths],
            )
        except Exception as e:
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=chosen_model,
                prompt=message,
                response=_strip_ansi(stdout_b.decode("utf-8", errors="replace")) if stdout_b else "",
                elapsed_s=self._now() - start,
                exit_code=getattr(proc, "returncode", None) if proc else None,
                error=f"{type(e).__name__}: {e}",
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in ctx_paths],
            )

        elapsed = self._now() - start
        stdout = _strip_ansi(stdout_b.decode("utf-8", errors="replace"))
        stderr = _strip_ansi(stderr_b.decode("utf-8", errors="replace"))
        rc = proc.returncode if proc else -1
        if self.output_format == "json":
            response, json_error = self._extract_json(stdout)
        else:
            response, json_error = stdout.strip(), None

        extras: dict[str, Any] = {
            "base_url": self.base_url,
            "config_path": str(self.config_path),
            "provider": self.provider_id,
        }
        if stderr:
            extras["stderr"] = stderr[:5000]

        # opencode exits 0 even when the API call fails, so a JSON error event is
        # authoritative over the exit code for failure detection.
        if rc != 0 or json_error is not None:
            if rc != 0:
                err = f"opencode exit {rc}; stderr_preview={stderr[:500]}"
            else:
                err = f"opencode error: {json_error}"
            return DispatchResult(
                status="failed",
                backend=self.name,
                model=chosen_model,
                prompt=message,
                response=response,
                elapsed_s=elapsed,
                exit_code=rc,
                error=err,
                packet_path=str(packet_path) if packet_path else None,
                context_files=[str(p) for p in ctx_paths],
                extras=extras,
            )
        return DispatchResult(
            status="completed",
            backend=self.name,
            model=chosen_model,
            prompt=message,
            response=response,
            elapsed_s=elapsed,
            exit_code=rc,
            error=None,
            packet_path=str(packet_path) if packet_path else None,
            context_files=[str(p) for p in ctx_paths],
            extras=extras,
        )


def _collect_text_parts(obj: Any) -> list[str]:
    """Recursively harvest assistant text from opencode JSON events.

    opencode's JSON event schema is not contractually stable across versions, so
    rather than bind to one exact shape we walk the structure and collect the
    ``text`` of any text *part* — a dict with ``type == "text"`` and a string
    ``text`` (the AI-SDK / opencode convention). We deliberately do NOT grab every
    ``text`` key (which would scoop up tool inputs / echoed user content); only
    proper text parts count.
    """
    out: list[str] = []
    if isinstance(obj, dict):
        if obj.get("type") == "text" and isinstance(obj.get("text"), str):
            out.append(obj["text"])
        for val in obj.values():
            if isinstance(val, (dict, list)):
                out.extend(_collect_text_parts(val))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_collect_text_parts(item))
    return out


def _format_error_event(obj: dict[str, Any]) -> str:
    """Build a concise message from an opencode ``{"type":"error",...}`` event."""
    err = obj.get("error")
    if not isinstance(err, dict):
        return str(err) if err else "opencode error event"
    data = err.get("data")
    if isinstance(data, dict):
        msg = data.get("message")
        status = data.get("statusCode")
        name = err.get("name")
        parts = [p for p in (name, msg) if p]
        text = ": ".join(str(p) for p in parts) if parts else "opencode error event"
        if status:
            text = f"{text} (HTTP {status})"
        return text
    return str(err.get("name") or "opencode error event")
