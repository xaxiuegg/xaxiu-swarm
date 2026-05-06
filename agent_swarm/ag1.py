"""AG#1 cross-engine meta-review helper (v0.3.0 G32).

Standardizes the AG#1 dispatch with source-trace verification baked in.

Background — why this exists:
    The V_CONV4g closeout (2026-05-06) surfaced a hybrid-tier false-positive
    risk: chat-tier cohort workers may extrapolate finding-shapes from one
    location to another without verifying the specific source code at the
    target location. Pro AG#1 may rubber-stamp these findings if the
    adjudication prompt only asks "does this make sense" rather than
    "verify against actual source."

    The V_CONV4g case: DI-DeepSeek-chat claimed `setSalvageByTypeActive`
    has the same stale-closure risk as 10 other setters V_CONV4g fixed.
    AG#1 (pro) initially sided with DI. A retraction-check (also pro, but
    with explicit source-trace prompt) revealed: setSalvageByTypeActive's
    consumer is an INLINE JSX handler, not a memoized useCallback —
    completely different category, no stale-closure risk by construction.

    DI was wrong; first AG#1 was wrong-by-omission (didn't verify). The
    retraction-check with source-trace prompt was right. Net cost of the
    error was a near-miss V_CONV4h wave that wasn't actually needed.

G32 enforces source-trace verification in every AG#1 dispatch by default.
The prompt template includes:
  1. Cohort situation context
  2. Per-engine findings (loaded from cohort report files)
  3. EXPLICIT verification requirement: "for each finding cited, locate
     it in the inlined source, verify the surrounding code matches the
     claim, mark FALSE POSITIVE if it doesn't"
  4. Decisive verdict ask
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_swarm.dispatch import dispatch_async
from agent_swarm.backends.base import Backend, DispatchResult


# G32: the verification clause that gets injected into every AG#1 prompt.
# Override via `verification_clause=` kwarg if you want a different stance.
DEFAULT_VERIFICATION_CLAUSE = """
**Source-trace verification (REQUIRED before adjudication):**
For each cohort finding cited below, you MUST:
  1. Locate the cited line(s) in the inlined source-file context.
  2. Verify the surrounding code matches the cohort worker's claim.
  3. If the claim is about a React stale-closure: verify the consumer is a
     memoized `useCallback` (not an inline JSX handler — inline handlers
     recreate every render and have no stale-closure risk by construction).
  4. If the claim cites a regex/pattern, verify it actually applies at the
     target site (don't accept "X has same pattern as Y" without checking).
  5. Mark any finding that fails source-trace as **FALSE POSITIVE** with a
     1-paragraph explanation of the category error.
  6. Only adjudicate findings that pass source-trace. Do NOT take the
     cohort report's framing at face value.

This requirement was added in agent-swarm v0.3.0 (G32) after the V_CONV4g
hybrid-tier retraction surfaced that chat-tier workers may pattern-extrapolate
without verifying targets. Without this clause, AG#1 may rubber-stamp
plausible-sounding but unverified claims.
""".strip()


def build_ag1_prompt(
    subject: str,
    cohort_reports: list[Path] | None = None,
    extra_context: str = "",
    base_question: str = "",
    verification_clause: str = DEFAULT_VERIFICATION_CLAUSE,
    target_words: int = 600,
) -> str:
    """G32: Construct an AG#1 cross-engine meta-review prompt.

    The cohort_reports are NOT inlined into the prompt itself — they're
    passed via `context_files` so the dispatch backend's G16 inlining
    handles them. This keeps the prompt small and lets the backend
    apply its prompt-cache where applicable.

    Args:
        subject: short description of the wave/artifact under review
            (e.g., "V_CONV4g Wave 43 hotfix"). Used to set context.
        cohort_reports: list of cohort worker report paths (just for
            documenting which reports the AG#1 saw; not inlined here).
        extra_context: any additional context to inject before the
            verification clause (e.g., cross-engine disagreement summary).
        base_question: the actual adjudication question
            (e.g., "verdict CLEAN/YELLOW/RED for promote?").
        verification_clause: the source-trace requirement text.
            Override only if you have a specific reason (e.g., adjudicating
            findings where source isn't needed).
        target_words: hint to the model for response length.

    Returns:
        Prompt string suitable for `dispatch_async(prompt, ...)`.
    """
    parts: list[str] = []
    parts.append(f"# AG#1 Cross-Engine Meta-Review — {subject}")
    parts.append("")
    parts.append("You are AG#1, the external-judge cross-engine adjudicator. "
                 "The cohort worker reports are inlined as context files. "
                 "The relevant source artifact(s) are also inlined as context. "
                 "Your job: synthesize cohort verdicts into a single decisive "
                 "verdict, but ONLY after source-trace verifying each cited finding.")
    parts.append("")

    if cohort_reports:
        parts.append("## Cohort reports under review")
        for r in cohort_reports:
            parts.append(f"  - {Path(r).name}")
        parts.append("")

    if extra_context:
        parts.append("## Cohort situation / disagreement summary")
        parts.append(extra_context)
        parts.append("")

    parts.append("## Verification requirement (G32)")
    parts.append(verification_clause)
    parts.append("")

    parts.append("## Adjudication ask")
    parts.append(base_question)
    parts.append("")
    parts.append(f"Target length: ~{target_words} words. Be decisive.")

    return "\n".join(parts)


async def ag1_meta_review(
    subject: str,
    cohort_reports: list[Path],
    source_files: list[Path] | None = None,
    base_question: str = "Final verdict CLEAN / YELLOW / RED for promote, with one-paragraph rationale.",
    extra_context: str = "",
    backend: str | Backend = "deepseek",
    model: str | None = None,
    deliverable_path: str | Path | None = None,
    timeout: int = 180,
    audit_dir: Path | None = None,
    verification_clause: str = DEFAULT_VERIFICATION_CLAUSE,
    target_words: int = 600,
    **kwargs: Any,
) -> DispatchResult:
    """G32: One-shot AG#1 cross-engine meta-review with source-trace baked in.

    Args:
        subject: short description (e.g., "V_CONV4g Wave 43 hotfix").
        cohort_reports: list of paths to per-engine cohort reports.
            These will be inlined as context_files so the AG#1 model sees
            them verbatim.
        source_files: list of source artifacts (e.g., V-files) to inline
            so AG#1 can source-trace findings. Optional but strongly
            recommended for code-review-shaped AG#1s.
        base_question: the verdict question (default ~CLEAN/YELLOW/RED).
        extra_context: optional summary of cross-engine disagreement etc.
        backend: backend name or Backend instance (default "deepseek").
        model: model override (default uses backend's default;
            `DEEPSEEK_MODEL` env var honored via v0.2.4 G31).
        deliverable_path: where to auto-write the AG#1 report (G17).
        timeout: dispatch timeout seconds.
        audit_dir: where to write audit jsonl.
        verification_clause: override the source-trace requirement
            (rarely needed; default is correct for code-review AG#1s).
        target_words: response-length hint.
        **kwargs: forwarded to dispatch_async.

    Returns:
        DispatchResult.
    """
    prompt = build_ag1_prompt(
        subject=subject,
        cohort_reports=cohort_reports,
        extra_context=extra_context,
        base_question=base_question,
        verification_clause=verification_clause,
        target_words=target_words,
    )

    # Combine cohort reports + source files into context_files for
    # G16 inlining
    context_files: list[Path] = list(cohort_reports)
    if source_files:
        context_files.extend(source_files)

    return await dispatch_async(
        prompt,
        backend=backend,
        is_packet=False,
        timeout=timeout,
        audit_dir=audit_dir,
        deliverable_path=deliverable_path,
        context_files=context_files,
        model=model,
        **kwargs,
    )
