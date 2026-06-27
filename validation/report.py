"""Final report assembly: gating by required_gates, the accept / reject / pending verdict,
and markdown rendering.

Сборка итогового отчёта: гейт по required_gates, вердикт accept / reject / pending
и markdown-рендер.
"""
from __future__ import annotations
import json


def build_report(cfg, requirements: dict, acceptance: dict, provenance: dict,
                 repro=None) -> dict:
    """Assemble the report and derive the verdict: reject on a failed required gate,
    pending on an unknown one, accept otherwise. The validator passport (provenance,
    repro) is a separate block that takes no part in the criteria.

    In: cfg, requirements (R-id → result), acceptance, provenance,
    repro (self-test | None). Out: report dict.

    Собирает отчёт и выводит вердикт: reject при проваленном required-гейте, pending при
    неизвестном, иначе accept. Паспорт валидатора (provenance, repro) — отдельный блок,
    в критериях не участвует.
    In: cfg, requirements (R-id → результат), acceptance, provenance,
    repro (self-test | None). Out: dict отчёта."""
    required = set(acceptance.get("required_gates",
                                  ["R1", "R3", "R5"]))
    summary = {}
    for rid, r in requirements.items():
        summary[rid] = r.get("pass")

    blocked = [rid for rid in required if summary.get(rid) is False]
    pending = [rid for rid in required if summary.get(rid) is None]
    verdict = "reject" if blocked else ("accept" if not pending else "pending")

    return {
        "config_id": cfg.get("config_id"),
        "config_version": cfg.get("config_version"),
        "verdict": verdict,
        "required_gates": sorted(required),
        "blocked_gates": blocked,
        "pending_gates": pending,
        "summary": summary,
        "requirements": requirements,
        # validator passport: logging about the validator itself, not a criterion
        "provenance": provenance,
        "repro": repro,
    }


def _badge(p):
    """Return a text badge for a pass status. In: True/False/None. Out: str.

    Текстовый бейдж статуса pass. In: True/False/None. Out: str."""
    return {True: "PASS", False: "FAIL", None: "PENDING"}.get(p, "-")


def to_markdown(report: dict) -> str:
    """Render the report as a markdown table covering verdict, metrics, and provenance.

    In: report (from build_report). Out: str (markdown).

    Рендерит отчёт в markdown-таблицу: вердикт, метрики, provenance.
    In: report (из build_report). Out: str (markdown)."""
    L = [f"# Валидация `{report.get('config_id')}` (v{report.get('config_version')})", ""]
    L.append(f"**Вердикт: {report['verdict'].upper()}** · required: {report['required_gates']}")
    if report["blocked_gates"]:
        L.append(f"- blocked: {report['blocked_gates']}")
    if report["pending_gates"]:
        L.append(f"- pending (обогащение/ресёрч): {report['pending_gates']}")
    L.append("")
    L.append("| Требование | Итог | Метрики |")
    L.append("|---|:--:|---|")
    for rid, r in report["requirements"].items():
        ms = []
        for mn, m in r["metrics"].items():
            tag = _badge(m["pass"])
            if m.get("requires"):
                tag = f"PENDING({m['requires']})"
            ms.append(f"{mn} {tag}")
        L.append(f"| {r.get('requirement', rid)} | {_badge(r.get('pass'))} | {'; '.join(ms)} |")
    # validator passport — logging about the validator itself, not a criterion
    L.append("")
    L.append("## Паспорт валидатора (не критерий)")
    L.append("")
    L.append(f"Provenance: {json.dumps(report['provenance'], ensure_ascii=False)}")
    if report.get("repro"):
        rc = report["repro"]["reproducibility_check"]
        L.append(f"Self-test (repro): {_badge(rc['pass'])} — два прогона, diff детерминированных метрик == 0")
    return "\n".join(L)
