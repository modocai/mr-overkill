"""Summary generation and PR commenting.

Ports ``_generate_summary`` from ``common.sh`` and ``_post_pr_comment``
from ``review-loop.sh``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from mr_overkill.models import FinalStatus

logger = logging.getLogger(__name__)


def generate_summary(
    title: str,
    log_dir: Path,
    current_branch: str,
    target_branch: str,
    max_loop: int,
    final_status: FinalStatus,
    extra_lines: list[str] | None = None,
) -> Path:
    """Generate ``summary.md`` from iteration logs.

    Returns the path to the generated summary file.
    """
    summary_path = log_dir / "summary.md"
    lines: list[str] = [f"# {title}", ""]

    if extra_lines:
        lines.extend(extra_lines)

    lines.extend([
        f"- **Branch**: {current_branch} → {target_branch}",
        f"- **Max iterations**: {max_loop}",
        f"- **Final status**: {final_status}",
        f"- **Timestamp**: {datetime.now(tz=UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "## Iteration Logs",
        "",
    ])

    # Parse review files
    review_files = sorted(log_dir.glob("review-*.json"))
    for rf in review_files:
        m = rf.stem.removeprefix("review-")
        try:
            iter_num = int(m)
        except ValueError:
            continue

        count = "?"
        verdict = "?"
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
            findings = data.get("findings", [])
            count = str(len(findings)) if isinstance(findings, list) else "?"
            verdict = data.get("overall_correctness", "?")
        except (json.JSONDecodeError, OSError, AttributeError, TypeError):
            pass

        lines.append(
            f"- **Iteration {iter_num}**: {count} findings, verdict: {verdict}"
        )

        # Self-review sub-iterations
        for sf in sorted(log_dir.glob(f"self-review-{iter_num}-*.json")):
            sub_m = sf.stem.removeprefix(f"self-review-{iter_num}-")
            sr_count = "?"
            sr_verdict = "?"
            try:
                sr_data = json.loads(sf.read_text(encoding="utf-8"))
                sr_findings = sr_data.get("findings", [])
                sr_count = (
                    str(len(sr_findings))
                    if isinstance(sr_findings, list)
                    else "?"
                )
                sr_verdict = sr_data.get("overall_correctness", "?")
            except (json.JSONDecodeError, OSError, AttributeError, TypeError):
                pass
            lines.append(
                f"  - Sub-iteration {sub_m}: {sr_count} findings, verdict: {sr_verdict}"
            )

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def post_pr_comment(
    pr_number: str,
    iteration: int,
    max_loop: int,
    review_json: dict[str, object],
    fix_file: Path | None = None,
    opinion_file: Path | None = None,
    self_review_summary: str = "",
    max_subloop: int = 0,
) -> bool:
    """Post iteration summary as PR comment using ``gh`` CLI.

    Returns ``True`` if posted successfully.
    """
    if not pr_number:
        return False

    findings = review_json.get("findings", [])
    overall = review_json.get("overall_correctness", "?")
    count = len(findings) if isinstance(findings, list) else 0

    # Build findings table
    table_rows: list[str] = []
    if isinstance(findings, list):
        for f in findings:
            if isinstance(f, dict):
                title = f.get("title", "")
                score = f.get("confidence_score", "")
                loc = f.get("code_location", {})
                fpath = loc.get("file_path", "") if isinstance(loc, dict) else ""
                line_start = ""
                lr = loc.get("line_range", {}) if isinstance(loc, dict) else {}
                if isinstance(lr, dict):
                    line_start = str(lr.get("start", ""))
                location = f"`{fpath}:{line_start}`" if fpath else ""
                table_rows.append(f"| {title} | {score} | {location} |")

    # Build fix summary
    fix_summary = ""
    if fix_file and fix_file.is_file():
        content = fix_file.read_text(encoding="utf-8")
        # Extract Fix Summary section
        in_section = False
        section_lines: list[str] = []
        for line in content.splitlines():
            if line.startswith("## Fix Summary"):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section:
                section_lines.append(line)
        fix_summary = "\n".join(section_lines).strip()

    # Build comment body
    body_parts: list[str] = [
        f"### AI Review — Iteration {iteration} / {max_loop}\n",
        f"**Overall**: {overall} ({count} findings)\n",
        "<details>\n<summary>Review Findings</summary>\n",
        "| Finding | Confidence | Location |",
        "|---------|-----------|----------|",
        *table_rows,
        "\n</details>\n",
        "<details>\n<summary>Fix Actions</summary>\n",
        fix_summary,
        "\n</details>",
    ]

    if opinion_file and opinion_file.is_file():
        opinion_content = opinion_file.read_text(encoding="utf-8")[:2000]
        body_parts.extend([
            "\n<details>\n<summary>Claude Opinion</summary>\n",
            opinion_content,
            "\n\n</details>",
        ])

    if self_review_summary:
        body_parts.extend([
            f"\n<details>\n<summary>Self-Review "
            f"({max_subloop} max sub-iterations)</summary>\n",
            self_review_summary,
            "</details>",
        ])

    body = "\n".join(body_parts)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(body)
        body_file = f.name

    try:
        result = subprocess.run(
            ["gh", "pr", "comment", pr_number, "--body-file", body_file],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("PR comment posted.")
            return True
        logger.warning("Failed to post PR comment: %s", result.stderr.strip())
        return False
    except FileNotFoundError:
        logger.warning("gh CLI not found — PR commenting disabled.")
        return False
    finally:
        Path(body_file).unlink(missing_ok=True)
