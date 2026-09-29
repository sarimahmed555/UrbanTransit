"""Human-readable summary of the same machine-readable decisions."""


def markdown(report):
    clean = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# UrbanTransit IQ SRS acceptance", "", f"Scope: **{report['scope']}**. Overall: **{report['overall_state']}**.",
             "", " | ".join(f"{k}: {v}" for k, v in report["counts"].items()), "",
             "| Requirement | Basis | State | Finding |", "|---|---|---|---|"]
    for row in report["checks"]:
        lines.append(f"| {clean(row['id'])} | {row['basis']} | {row['state']} | {clean(row['reason'])} |")
    lines.extend(["", "## Missing evidence / actions", ""])
    lines.extend(f"- **{a['id']}** ({a['state']}): {clean(a['action'])}. {clean(a['missing_or_failed'])}" for a in report["actions"])
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {text}" for text in report["limitations"])
    return "\n".join(lines)+"\n"
