from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path("app/templates")

ROUTE_PATTERNS = [
    re.compile(r'href=["\'](/[^"\']+)["\']'),
    re.compile(r"location\.href\s*=\s*['\"](/[^'\"]+)['\"]"),
    re.compile(r"fetch\(\s*['\"](/[^'\"]+)['\"]"),
]


def extract_routes(text: str) -> set[str]:
    routes: set[str] = set()
    for pat in ROUTE_PATTERNS:
        routes.update(m.group(1).strip() for m in pat.finditer(text))
    return {r for r in routes if r and not r.startswith("//")}


def extract_buttons(text: str) -> list[tuple[str, str]]:
    tags = re.findall(r"<button([^>]*)>(.*?)</button>", text, flags=re.IGNORECASE | re.DOTALL)
    out = []
    for attrs, inner in tags:
        label = re.sub(r"\s+", " ", re.sub(r"<.*?>", "", inner)).strip()
        out.append((attrs, label))
    return out


def has_handler(attrs: str, text: str, label: str) -> bool:
    if "onclick=" in attrs.lower():
        return True
    m = re.search(r'id=["\']([^"\']+)["\']', attrs, flags=re.IGNORECASE)
    if not m:
        return False
    btn_id = m.group(1)
    # Standard binding patterns in templates.
    patterns = [
        rf"getElementById\(['\"]{re.escape(btn_id)}['\"]\)\.addEventListener",
        rf"el\(['\"]{re.escape(btn_id)}['\"]\)\.addEventListener",
        rf"{re.escape(btn_id)}\.onclick",
        rf"document\.getElementById\(['\"]{re.escape(btn_id)}['\"]\)\.onclick",
    ]
    return any(re.search(p, text) for p in patterns)


def main() -> None:
    templates = sorted(TEMPLATES_DIR.glob("*.html"))
    report_lines: list[str] = []
    all_routes: set[str] = set()
    unbound_buttons: list[tuple[str, str]] = []

    for tpl in templates:
        text = tpl.read_text(encoding="utf-8", errors="ignore")
        routes = extract_routes(text)
        all_routes.update(routes)
        buttons = extract_buttons(text)
        for attrs, label in buttons:
            if not has_handler(attrs, text, label):
                # Skip decorative/submit buttons that are naturally handled by form submit.
                if 'type="submit"' in attrs.lower() or "type='submit'" in attrs.lower():
                    continue
                unbound_buttons.append((tpl.name, label or "[no label]"))

        report_lines.append(f"{tpl.name}: routes={len(routes)} buttons={len(buttons)}")

    report_lines.append("")
    report_lines.append("Discovered Routes")
    for r in sorted(all_routes):
        report_lines.append(f"- {r}")

    report_lines.append("")
    report_lines.append("Buttons Without Explicit Handler")
    if not unbound_buttons:
        report_lines.append("- none")
    else:
        for tpl_name, label in unbound_buttons:
            report_lines.append(f"- {tpl_name}: {label}")

    out = Path("button_route_qa_report.txt")
    out.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"QA report generated: {out}")


if __name__ == "__main__":
    main()
