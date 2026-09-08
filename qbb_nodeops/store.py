from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LoadRecord:
    name: str
    path: str
    success: bool
    elapsed_sec: float
    reason: str = ""
    traceback: str = ""
    module_parent: str = "custom_nodes"
    # ok | partial | failed
    status: str = "ok"
    partial_issues: list[str] = field(default_factory=list)
    captured_log: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.status in ("failed", "partial")


@dataclass
class LoadReport:
    records: list[LoadRecord] = field(default_factory=list)
    hook_installed: bool = False
    summary_printed: bool = False
    summary_scheduled: bool = False

    def add(self, record: LoadRecord) -> None:
        self.records.append(record)

    @property
    def failures(self) -> list[LoadRecord]:
        return [r for r in self.records if r.status == "failed"]

    @property
    def partials(self) -> list[LoadRecord]:
        return [r for r in self.records if r.status == "partial"]

    @property
    def successes(self) -> list[LoadRecord]:
        return [r for r in self.records if r.status == "ok"]

    @property
    def attention_items(self) -> list[LoadRecord]:
        return [r for r in self.records if r.needs_attention]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hook_installed": self.hook_installed,
            "total": len(self.records),
            "success_count": len(self.successes),
            "failure_count": len(self.failures),
            "partial_count": len(self.partials),
            "attention_count": len(self.attention_items),
            "failures": [asdict(r) for r in self.failures],
            "partials": [asdict(r) for r in self.partials],
            "attention": [asdict(r) for r in self.attention_items],
            "records": [asdict(r) for r in self.records],
        }

    def to_log_text(self) -> str:
        """Human-readable full load log for export."""
        from datetime import datetime

        lines: list[str] = []
        lines.append("QBB NodeOps - Full Custom Node Load Log")
        lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
        lines.append(f"Hook installed: {self.hook_installed}")
        lines.append(
            f"Summary: total={len(self.records)} ok={len(self.successes)} "
            f"partial={len(self.partials)} failed={len(self.failures)}"
        )
        lines.append("=" * 72)

        for idx, r in enumerate(self.records, 1):
            lines.append("")
            lines.append(f"[{idx}/{len(self.records)}] {r.name}")
            lines.append("-" * 72)
            lines.append(f"Status : {r.status.upper()}")
            lines.append(f"Path   : {r.path}")
            lines.append(f"Time   : {r.elapsed_sec:.3f}s")
            if r.reason:
                lines.append(f"Reason : {r.reason}")
            if r.partial_issues:
                lines.append("Partial issues:")
                for i, issue in enumerate(r.partial_issues, 1):
                    lines.append(f"  ---- issue #{i} ----")
                    lines.append(issue.rstrip())
            if r.traceback and r.status == "failed":
                lines.append("Traceback:")
                lines.append(r.traceback.rstrip())
            if r.captured_log.strip():
                lines.append("Captured output during load:")
                lines.append(r.captured_log.rstrip())
            lines.append("=" * 72)

        if self.failures:
            lines.append("")
            lines.append("FAILED PACKAGES")
            lines.append("-" * 72)
            for r in self.failures:
                lines.append(f"- {r.name}: {r.reason or 'IMPORT FAILED'}")

        if self.partials:
            lines.append("")
            lines.append("PARTIAL PACKAGES (loaded with internal node/module issues)")
            lines.append("-" * 72)
            for r in self.partials:
                lines.append(f"- {r.name}: {r.reason or 'internal issues'}")

        lines.append("")
        lines.append("End of log.")
        lines.append("")
        return "\n".join(lines)


REPORT = LoadReport()

# Strong signals that a sub-node / submodule failed while the package still loaded.
# Keep this relatively strict to avoid false positives from unrelated log noise.
_ISSUE_PATTERNS = [
    re.compile(r"traceback \(most recent call last\):", re.I),
    re.compile(r"\bImportError\b"),
    re.compile(r"\bModuleNotFoundError\b"),
    re.compile(r"failed to import", re.I),
    re.compile(r"failed to load", re.I),
    re.compile(r"failed importing", re.I),
    re.compile(r"cannot import", re.I),
    re.compile(r"import failed", re.I),
    re.compile(r"error (?:loading|importing)", re.I),
    re.compile(r"could not (?:load|import)", re.I),
    re.compile(r"unable to (?:load|import)", re.I),
    re.compile(r"exception when loading", re.I),
    re.compile(r"skipping .*node", re.I),
    re.compile(r"跳过.*(节点|模块)"),
    re.compile(r"(节点|模块).*(加载失败|导入失败)"),
    re.compile(r"(加载失败|导入失败)"),
]


def _is_traceback_continuation(line: str) -> bool:
    s = line.rstrip("\n")
    if not s:
        return False
    if s.startswith(" ") or s.startswith("\t"):
        return True
    if s.startswith("File "):
        return True
    if re.match(
        r"^[A-Za-z_][\w\.]* (?:Error|Exception|Warning|Exit|Interrupt)(?::|\s|$)",
        s,
    ):
        return True
    if s.startswith("During handling of the above"):
        return True
    if s.startswith("The above exception was the direct cause"):
        return True
    return False


def extract_partial_issues(captured_text: str) -> list[str]:
    """Split captured stdout/logging into issue chunks worth surfacing."""
    text = (captured_text or "").strip()
    if not text:
        return []

    issues: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("Traceback (most recent call last):"):
            block = [line]
            i += 1
            while i < len(lines) and _is_traceback_continuation(lines[i]):
                block.append(lines[i])
                i += 1
            issues.append("\n".join(block))
            continue

        if any(p.search(line) for p in _ISSUE_PATTERNS):
            block = [line.rstrip()]
            i += 1
            # Keep indented follow-up lines with the issue
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                block.append(lines[i].rstrip())
                i += 1
            issues.append("\n".join(block))
            continue

        i += 1

    seen: set[str] = set()
    out: list[str] = []
    for issue in issues:
        key = issue.strip()
        if not key or key in seen:
            continue
        if "QBB LoadHighlight" in key or "LOADING CUSTOM NODE" in key:
            continue
        seen.add(key)
        out.append(key)
    return out
