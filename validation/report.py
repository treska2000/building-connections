"""report.py — сборка итогового отчёта: гейт по required_gates, вердикт
accept / reject / pending, markdown-рендер.

report.py — final report assembly: gating by required_gates, the
accept / reject / pending verdict, markdown rendering.
"""
from __future__ import annotations
import json


def build_report(cfg, requirements: dict, acceptance: dict, provenance: dict,
                 determinism, repro=None) -> dict:
    """Собирает отчёт и выводит вердикт: reject при проваленном required-гейте,
    pending при неизвестном, иначе accept. Вход: cfg, requirements (R-id → результат),
    acceptance, provenance, determinism, repro. Выход: dict отчёта.
    Assembles the report and derives the verdict: reject on a failed required gate,
    pending on an unknown one, accept otherwise. In: cfg, requirements (R-id → result),
    acceptance, provenance, determinism, repro. Out: report dict."""
    required = set(acceptance.get("required_gates",
                                  ["R1", "R2", "R3", "R5", "R7"]))
    summary = {}
    for rid, r in requirements.items():
        summary[rid] = r.get("pass")
    if repro is not None:
        summary["R7"] = repro["reproducibility_check"]["pass"]

    blocked = [rid for rid in required if summary.get(rid) is False]
    pending = [rid for rid in required if summary.get(rid) is None]
    verdict = "reject" if blocked else ("accept" if not pending else "pending")

    return {
        "config_id": cfg.get("config_id"),
        "config_version": cfg.get("config_version"),
        "provenance": provenance,
        "verdict": verdict,
        "required_gates": sorted(required),
        "blocked_gates": blocked,
        "pending_gates": pending,
        "summary": summary,
        "requirements": requirements,
        "determinism_flags": determinism,
        "repro": repro,
    }


def _badge(p):
    """Эмодзи-бейдж статуса pass. Вход: True/False/None. Выход: str.
    Emoji badge for a pass status. In: True/False/None. Out: str."""
    return {True: "✅", False: "❌", None: "⏳"}.get(p, "·")


def to_markdown(report: dict) -> str:
    """Рендерит отчёт в markdown-таблицу (вердикт, метрики, provenance).
    Вход: report (из build_report). Выход: str (markdown).
    Renders the report as a markdown table (verdict, metrics, provenance).
    In: report (from build_report). Out: str (markdown)."""
    L = [f"# Валидация `{report.get('config_id')}` (v{report.get('config_version')})", ""]
    L.append(f"**Вердикт: {report['verdict'].upper()}** · required: {report['required_gates']}")
    if report["blocked_gates"]:
        L.append(f"- ❌ blocked: {report['blocked_gates']}")
    if report["pending_gates"]:
        L.append(f"- ⏳ pending (обогащение/ресёрч): {report['pending_gates']}")
    L.append("")
    L.append("| Требование | Итог | Метрики |")
    L.append("|---|:--:|---|")
    for rid, r in report["requirements"].items():
        ms = []
        for mn, m in r["metrics"].items():
            tag = _badge(m["pass"])
            if m.get("requires"):
                tag = f"⏳({m['requires']})"
            ms.append(f"{mn} {tag}")
        L.append(f"| {r.get('requirement', rid)} | {_badge(r.get('pass'))} | {'; '.join(ms)} |")
    if report.get("repro"):
        rc = report["repro"]["reproducibility_check"]
        L.append(f"| R7 · Воспроизводимость | {_badge(rc['pass'])} | reproducibility_check {_badge(rc['pass'])} |")
    L.append("")
    L.append(f"Provenance: {json.dumps(report['provenance'], ensure_ascii=False)}")
    return "\n".join(L)
