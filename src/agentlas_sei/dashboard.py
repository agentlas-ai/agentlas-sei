from __future__ import annotations

import html
import re
import secrets
import threading
import webbrowser
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .i18n import SUPPORTED_LOCALES, resolve_locale, translate
from .store import ProjectStore

RULE_COPY_KEYS = {
    "rule:silent-exception-handler": ("rule_silent_title", "rule_silent_meaning"),
    "rule:fallback-observability-gap": (
        "rule_fallback_title",
        "rule_fallback_meaning",
    ),
    "rule:runtime-outcome-unknown": ("rule_runtime_title", "rule_runtime_meaning"),
    "rule:missing-intent-interview": ("rule_intent_title", "rule_intent_meaning"),
    "rule:unaccepted-intent": ("rule_accept_title", "rule_accept_meaning"),
    "rule:missing-user-flows": ("rule_flow_title", "rule_flow_meaning"),
}

SEVERITY_KEYS = {
    "critical": "severity_critical",
    "high": "severity_high",
    "medium": "severity_medium",
    "low": "severity_low",
}

_CODE_PATH_PATTERN = re.compile(
    r"(?P<path>[A-Za-z0-9_.@+()\-]+(?:/[A-Za-z0-9_.@+()[\]\-]+)+"
    r"\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|swift|rb|php|cs|"
    r"cpp|cc|c|h|hpp|vue|svelte))"
)


def _latest_by(records: Sequence[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        value = record.get(key)
        if not isinstance(value, str):
            continue
        if value not in latest:
            order.append(value)
        latest[value] = record
    return [latest[value] for value in order]


def _code_references(project_root: Path, observation: str) -> list[dict[str, str]]:
    root = project_root.resolve()
    references: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _CODE_PATH_PATTERN.finditer(observation):
        relative = match.group("path")
        if relative in seen:
            continue
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            continue
        seen.add(relative)
        references.append(
            {
                "label": relative,
                "editorUrl": "vscode://file" + quote(candidate.as_posix(), safe="/:"),
            }
        )
        if len(references) >= 5:
            break
    return references


def _plain_finding(
    project_root: Path,
    finding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "findingId": str(finding.get("findingId", "")),
        "ruleId": str(finding.get("ruleId", "unknown")),
        "rawTitle": str(finding.get("title", "")),
        "severity": str(finding.get("severity", "medium")),
        "state": str(finding.get("state", "candidate")),
        "observation": str(finding.get("observation", "")),
        "nextAction": str(finding.get("nextAction", "")),
        "codeRefs": _code_references(
            project_root,
            str(finding.get("observation", "")),
        ),
    }


def _micro_flows(flow: dict[str, Any]) -> list[dict[str, Any]]:
    micros: list[dict[str, Any]] = []
    for meso in flow.get("mesoFlows", []):
        if not isinstance(meso, dict):
            continue
        for micro in meso.get("microFlows", []):
            if isinstance(micro, dict):
                micros.append(micro)
    return micros


def _code_link_counts(flows: Sequence[dict[str, Any]]) -> tuple[int, int]:
    mapped = 0
    total = 0
    for flow in flows:
        for micro in _micro_flows(flow):
            total += 1
            code_links = micro.get("codeLinks", {})
            if isinstance(code_links, dict) and code_links.get("state") == "mapped":
                mapped += 1
    return mapped, total


def build_dashboard_snapshot(project_root: Path) -> dict[str, Any]:
    store = ProjectStore(project_root)
    store.require_initialized()
    findings = [
        _plain_finding(project_root, item)
        for item in _latest_by(store.records("findings"), "ruleId")
        if item.get("state") != "refuted"
    ]
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda item: severity_order.get(item["severity"], 9))
    flows = _latest_by(store.records("flows"), "flowId")
    mapped, total = _code_link_counts(flows)
    return {
        "schemaVersion": "sei.dashboard.v2",
        "project": project_root.name,
        "projectRoot": str(project_root.resolve()),
        "summary": {
            "flowCount": len(flows),
            "findingCount": len(findings),
            "codeLinkedCount": mapped,
            "codeLinkTotal": total,
        },
        "findings": findings,
        "flows": flows,
    }


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _t(locale: str, key: str, **values: Any) -> str:
    return translate(locale, key, values)


def _localized_finding(
    finding: dict[str, Any],
    locale: str,
) -> dict[str, Any]:
    title_key, meaning_key = RULE_COPY_KEYS.get(
        finding["ruleId"],
        ("unknown_issue", "unknown_meaning"),
    )
    title = _t(locale, title_key)
    if title_key == "unknown_issue" and finding.get("rawTitle"):
        title = str(finding["rawTitle"])
    return {
        **finding,
        "title": title,
        "meaning": _t(locale, meaning_key),
        "severityLabel": _t(
            locale,
            SEVERITY_KEYS.get(finding["severity"], "severity_medium"),
        ),
    }


def _code_state(
    code_links: dict[str, Any],
    locale: str,
) -> tuple[str, int, int]:
    entry = code_links.get("entry", [])
    exit_links = code_links.get("exit", [])
    entries = entry if isinstance(entry, list) else []
    exits = exit_links if isinstance(exit_links, list) else []
    linked = len(entries) + len(exits)
    state = str(code_links.get("state", "unmapped"))
    if state == "mapped":
        label = _t(locale, "code_linked")
    elif state == "partial":
        label = _t(locale, "code_partial")
    else:
        label = _t(locale, "code_unmapped")
    return label, linked, 2


def _flow_html(flow: dict[str, Any], locale: str) -> str:
    meso_blocks: list[str] = []
    flow_mapped = 0
    flow_total = 0
    for meso in flow.get("mesoFlows", []):
        if not isinstance(meso, dict):
            continue
        micro_rows: list[str] = []
        for micro in meso.get("microFlows", []):
            if not isinstance(micro, dict):
                continue
            flow_total += 1
            raw_links = micro.get("codeLinks", {})
            code_links = raw_links if isinstance(raw_links, dict) else {}
            state_label, linked, _ = _code_state(code_links, locale)
            if code_links.get("state") == "mapped":
                flow_mapped += 1
            transitions = micro.get("transitions", [])
            observable = ""
            if isinstance(transitions, list) and transitions:
                first_transition = transitions[0]
                if isinstance(first_transition, dict):
                    observable = str(first_transition.get("observable", ""))
            micro_rows.append(
                "<div class='micro-row'>"
                "<div><span class='level-label'>"
                + _esc(_t(locale, "small_action"))
                + "</span><strong>"
                + _esc(micro.get("name", ""))
                + "</strong><p>"
                + _esc(observable)
                + "</p></div><span class='code-state' data-linked='"
                + _esc(linked)
                + "'>"
                + _esc(state_label)
                + "</span></div>"
            )
        meso_blocks.append(
            "<section class='meso-block'><div class='meso-heading'>"
            "<span class='level-label'>"
            + _esc(_t(locale, "middle_step"))
            + "</span><strong>"
            + _esc(meso.get("name", ""))
            + "</strong></div>"
            + "".join(micro_rows)
            + "</section>"
        )
    return (
        "<details class='flow-row'><summary><span><span class='level-label'>"
        + _esc(_t(locale, "large_flow"))
        + "</span><strong>"
        + _esc(flow.get("name", ""))
        + "</strong></span><span class='flow-count'>"
        + _esc(
            _t(
                locale,
                "code_count",
                mapped=flow_mapped,
                total=flow_total,
            )
        )
        + "</span></summary><div class='flow-body'>"
        + "".join(meso_blocks)
        + "</div></details>"
    )


def _language_links(
    access_path: str,
    selected_finding_id: str,
    current_locale: str,
) -> str:
    labels = {"ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文"}
    links: list[str] = []
    for locale in SUPPORTED_LOCALES:
        query = urlencode({"lang": locale, "finding": selected_finding_id})
        active = " active" if locale == current_locale else ""
        current = " aria-current='page'" if locale == current_locale else ""
        links.append(
            "<a class='language-link"
            + active
            + "' lang='"
            + _esc(locale)
            + "'"
            + current
            + " href='"
            + _esc(access_path)
            + "?"
            + _esc(query)
            + "'>"
            + _esc(labels[locale])
            + "</a>"
        )
    return "".join(links)


def render_dashboard_html(
    snapshot: dict[str, Any],
    locale: str = "ko",
    selected_finding_id: str | None = None,
    access_path: str = "",
) -> str:
    locale = resolve_locale(locale)
    findings = [
        _localized_finding(item, locale) for item in snapshot.get("findings", [])
    ]
    selected = next(
        (
            item
            for item in findings
            if item.get("findingId") == selected_finding_id
        ),
        findings[0] if findings else None,
    )
    selected_id = str(selected.get("findingId", "")) if selected else ""
    issue_rows: list[str] = []
    for finding in findings:
        query = urlencode({"lang": locale, "finding": finding["findingId"]})
        active = " selected" if finding["findingId"] == selected_id else ""
        current = (
            " aria-current='page'"
            if finding["findingId"] == selected_id
            else ""
        )
        issue_rows.append(
            "<a class='issue-row"
            + active
            + "'"
            + current
            + " href='"
            + _esc(access_path)
            + "?"
            + _esc(query)
            + "'><span class='severity "
            + _esc(finding["severity"])
            + "'>"
            + _esc(finding["severityLabel"])
            + "</span><strong>"
            + _esc(finding["title"])
            + "</strong><span class='impact'>"
            + _esc(_t(locale, "impact"))
            + ": "
            + _esc(_t(locale, "impact_unmapped"))
            + "</span></a>"
        )

    if selected:
        code_refs = selected.get("codeRefs", [])
        code_html = "".join(
            "<a class='code-link' href='"
            + _esc(item["editorUrl"])
            + "'>"
            + _esc(item["label"])
            + "</a>"
            for item in code_refs
        )
        if not code_html:
            code_html = "<span class='empty-inline'>" + _esc(
                _t(locale, "no_code")
            ) + "</span>"
        detail_html = (
            "<div class='detail-heading'><span class='severity "
            + _esc(selected["severity"])
            + "'>"
            + _esc(selected["severityLabel"])
            + "</span><span class='candidate-note'>"
            + _esc(_t(locale, "automatic_candidate"))
            + "</span></div><h2>"
            + _esc(selected["title"])
            + "</h2><p class='meaning'>"
            + _esc(selected["meaning"])
            + "</p><div class='flow-trail'>"
            + "".join(
                "<div><span>"
                + _esc(_t(locale, key))
                + "</span><strong>"
                + _esc(_t(locale, "not_connected"))
                + "</strong></div>"
                for key in ("large_flow", "middle_step", "small_action")
            )
            + "</div><dl class='evidence-list'><div><dt>"
            + _esc(_t(locale, "observation"))
            + "</dt><dd>"
            + _esc(selected["observation"])
            + "</dd></div><div><dt>"
            + _esc(_t(locale, "next_check"))
            + "</dt><dd>"
            + _esc(selected["nextAction"])
            + "</dd></div><div><dt>"
            + _esc(_t(locale, "code_connection"))
            + "</dt><dd class='code-links'>"
            + code_html
            + "</dd></div></dl>"
        )
    else:
        detail_html = "<p class='empty-state'>" + _esc(
            _t(locale, "no_findings")
        ) + "</p>"

    flow_rows = "".join(
        _flow_html(flow, locale) for flow in snapshot.get("flows", [])
    )
    if not flow_rows:
        flow_rows = "<p class='empty-state'>" + _esc(
            _t(locale, "no_flows")
        ) + "</p>"
    summary = snapshot["summary"]
    summary_text = _t(
        locale,
        "summary",
        findings=summary["findingCount"],
        flows=summary["flowCount"],
        mapped=summary["codeLinkedCount"],
        total=summary["codeLinkTotal"],
    )
    language_links = _language_links(
        access_path,
        selected_id,
        locale,
    )
    return """<!doctype html>
<html lang="{locale}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{project} · SEI</title>
<style>
:root{{--ink:#111713;--muted:#67706a;--faint:#8b938e;--line:#dde2df;
--paper:#fbfcfb;--surface:#fff;--teal:#0d5a49;--teal-soft:#edf6f3;
--critical:#c52b27;--high:#a34218;--medium:#a66300;--low:#32734c;
--focus:#1677ff}}
*{{box-sizing:border-box}} html{{background:var(--paper)}} body{{margin:0;
color:var(--ink);background:var(--paper);font:15px/1.58 -apple-system,
BlinkMacSystemFont,"Segoe UI","Noto Sans KR","Noto Sans JP","Noto Sans SC",
sans-serif}} a{{color:inherit}} a:focus-visible,summary:focus-visible{{
outline:3px solid var(--focus);outline-offset:3px;border-radius:4px}}
.app-header{{height:92px;padding:0 32px;display:flex;align-items:center;
justify-content:space-between;gap:24px;background:var(--surface);
border-bottom:1px solid var(--line)}} .brand{{display:flex;align-items:baseline;
gap:20px;min-width:0}} h1{{margin:0;font-size:36px;letter-spacing:-.045em;
line-height:1}} .product-label{{color:var(--muted);font-size:18px;
font-weight:650}} .header-meta{{display:flex;align-items:center;gap:22px}}
.summary{{font-size:15px;font-weight:650;white-space:nowrap}}
.language-nav{{display:flex;gap:10px;align-items:center}}
.language-link{{font-size:12px;color:var(--muted);text-decoration:none;
border-bottom:1px solid transparent}} .language-link:hover,
.language-link.active{{color:var(--teal);border-color:currentColor}}
.dashboard{{min-height:calc(100vh - 92px);display:grid;
grid-template-columns:minmax(320px,35%) minmax(0,65%)}}
.issues-panel{{padding:34px 22px 42px 32px;border-right:1px solid var(--line);
background:#fafbfa}} .detail-panel{{padding:34px 36px 64px}}
.panel-title{{margin:0 0 24px;font-size:20px;letter-spacing:-.02em}}
.issue-list{{display:grid}} .issue-row{{display:grid;gap:7px;padding:20px 16px;
text-decoration:none;border-top:1px solid var(--line);position:relative}}
.issue-row:last-child{{border-bottom:1px solid var(--line)}}
.issue-row:hover{{background:#f4f7f5}} .issue-row.selected{{
background:var(--teal-soft)}} .issue-row.selected::before{{content:"";
position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--teal)}}
.issue-row strong{{font-size:17px;line-height:1.36;letter-spacing:-.012em}}
.severity{{font-size:12px;font-weight:750;width:max-content}}
.severity.critical{{color:var(--critical)}} .severity.high{{color:var(--high)}}
.severity.medium{{color:var(--medium)}} .severity.low{{color:var(--low)}}
.impact{{color:var(--muted);font-size:13px}} .detail-heading{{display:flex;
align-items:center;gap:12px;margin-bottom:14px}} .candidate-note{{
color:var(--muted);font-size:12px}} .detail-panel h2{{font-size:26px;
line-height:1.3;letter-spacing:-.028em;margin:0 0 8px}}
.meaning{{font-size:16px;color:#4e5751;max-width:760px;margin:0}}
.flow-trail{{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;
margin:38px 0 28px;padding-bottom:28px;border-bottom:1px solid var(--line)}}
.flow-trail div{{display:grid;gap:9px}} .flow-trail span,.level-label{{
font-size:11px;font-weight:750;color:var(--muted);letter-spacing:.04em;
text-transform:uppercase}} .flow-trail strong{{padding:16px 18px;
border:1px solid var(--line);border-radius:8px;background:var(--surface);
color:var(--faint)}} .evidence-list{{margin:0}} .evidence-list>div{{
display:grid;grid-template-columns:150px 1fr;gap:30px;padding:22px 0;
border-bottom:1px solid var(--line)}} dt{{font-weight:750}} dd{{margin:0;
color:#3e4641;overflow-wrap:anywhere}} .code-links{{display:flex;flex-wrap:wrap;
gap:8px}} .code-link{{font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,
monospace;color:var(--teal);text-underline-offset:3px;overflow-wrap:anywhere}}
.empty-inline,.empty-state{{color:var(--muted)}} .flows-section{{
margin-top:38px}} .flows-section>h2{{font-size:19px;margin:0 0 10px}}
.flow-row{{border-top:1px solid var(--line)}} .flow-row:last-child{{
border-bottom:1px solid var(--line)}} .flow-row summary{{cursor:pointer;
list-style-position:outside;padding:17px 6px;display:flex;
justify-content:space-between;align-items:center;gap:20px}}
.flow-row summary>span:first-child{{display:flex;align-items:baseline;gap:14px}}
.flow-count{{font-size:12px;color:var(--muted)}} .flow-body{{
padding:0 6px 12px 28px}} .meso-block{{border-top:1px solid #edf0ee;
padding:15px 0}} .meso-heading{{display:flex;gap:12px;align-items:baseline;
margin-bottom:8px}} .micro-row{{display:grid;grid-template-columns:1fr auto;
gap:20px;padding:12px 0 12px 20px;border-top:1px solid #f0f2f1}}
.micro-row div{{display:grid;gap:3px}} .micro-row p{{margin:0;color:var(--muted);
font-size:13px;max-width:780px}} .code-state{{font-size:12px;
color:var(--muted);white-space:nowrap;align-self:center}}
@media(max-width:920px){{.app-header{{height:auto;min-height:92px;
align-items:flex-start;padding:22px 24px;flex-direction:column;gap:16px}}
.header-meta{{width:100%;justify-content:space-between;flex-wrap:wrap}}
.dashboard{{display:block}}.issues-panel{{border-right:0;
border-bottom:1px solid var(--line);padding:28px 24px}}
.detail-panel{{padding:30px 24px 54px}}}}
@media(max-width:620px){{.brand{{gap:12px;flex-wrap:wrap}}h1{{font-size:30px}}
.product-label{{font-size:15px}}.header-meta{{display:grid;gap:12px}}
.summary{{white-space:normal}}.language-nav{{flex-wrap:wrap}}
.flow-trail{{grid-template-columns:1fr;gap:10px;margin-top:28px}}
.evidence-list>div{{grid-template-columns:1fr;gap:7px}}
.flow-row summary>span:first-child{{display:grid;gap:3px}}
.micro-row{{grid-template-columns:1fr;padding-left:8px}}
.code-state{{justify-self:start}}}}
@media(forced-colors:active){{.issue-row.selected{{outline:2px solid CanvasText}}
.issue-row.selected::before{{display:none}}}}
</style></head><body>
<header class="app-header"><div class="brand"><h1>{project}</h1>
<span class="product-label">{product_diagnosis}</span></div>
<div class="header-meta"><span class="summary">{summary}</span>
<nav class="language-nav" aria-label="{language}">{language_links}</nav></div>
</header>
<main class="dashboard"><aside class="issues-panel">
<h2 class="panel-title">{issues}</h2>
<nav class="issue-list" aria-label="{issues}">{issue_rows}</nav>
</aside><section class="detail-panel">
<h2 class="panel-title">{issue_and_flow}</h2>{detail}
<section class="flows-section"><h2>{flows_title}</h2>{flow_rows}</section>
</section></main></body></html>""".format(
        locale=_esc(locale),
        project=_esc(snapshot["project"]),
        product_diagnosis=_esc(_t(locale, "product_diagnosis")),
        summary=_esc(summary_text),
        language=_esc(_t(locale, "language")),
        language_links=language_links,
        issues=_esc(_t(locale, "issues")),
        issue_and_flow=_esc(_t(locale, "issue_and_flow")),
        issue_rows="".join(issue_rows)
        or "<p class='empty-state'>" + _esc(_t(locale, "no_findings")) + "</p>",
        detail=detail_html,
        flows_title=_esc(_t(locale, "flows")),
        flow_rows=flow_rows,
    )


class DashboardServer(ThreadingHTTPServer):
    snapshot: dict[str, Any]
    access_token: str
    default_locale: str


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        expected = f"/{self.server.access_token}"
        if parsed.path.rstrip("/") != expected:
            self.send_error(404)
            return
        query = parse_qs(parsed.query, keep_blank_values=False)
        requested_locale = query.get("lang", [self.server.default_locale])[0]
        selected_finding = query.get("finding", [None])[0]
        body = render_dashboard_html(
            self.server.snapshot,
            locale=requested_locale,
            selected_finding_id=selected_finding,
            access_path=expected,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_dashboard_server(
    snapshot: dict[str, Any],
    port: int = 0,
    locale: str = "auto",
) -> tuple[DashboardServer, str]:
    server = DashboardServer(("127.0.0.1", port), DashboardHandler)
    server.snapshot = snapshot
    server.access_token = secrets.token_urlsafe(18)
    server.default_locale = resolve_locale(locale)
    url = f"http://127.0.0.1:{server.server_port}/{server.access_token}"
    return server, url


def serve_dashboard(
    project_root: Path,
    port: int = 0,
    open_browser: bool = True,
    locale: str = "auto",
) -> None:
    selected_locale = resolve_locale(locale)
    snapshot = build_dashboard_snapshot(project_root)
    server, url = create_dashboard_server(snapshot, port, selected_locale)
    print(f"{_t(selected_locale, 'dashboard_url')}: {url}")
    print(_t(selected_locale, "stop_server"))
    if open_browser:
        threading.Timer(0.25, webbrowser.open_new_tab, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
