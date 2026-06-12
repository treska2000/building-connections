"""report.py — сборка отчёта, гейт, вердикт, markdown."""
from __future__ import annotations
import json


def build_report(cfg, requirements: dict, acceptance: dict, provenance: dict,
                 determinism, repro=None) -> dict:
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
    return {True: "✅", False: "❌", None: "⏳"}.get(p, "·")


def to_markdown(report: dict) -> str:
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
