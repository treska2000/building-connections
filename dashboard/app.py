"""Connections benchmark explorer.

Streamlit dashboard over reports/bench_results.csv. Splits model SKILL from
harness REACH, and lets you slice by model / mode / domain / verdict and drill
into individual runs (gold vs model answer + Drive link).

Run locally:
    pip install -r dashboard/requirements.txt
    streamlit run dashboard/app.py
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Connections bench", layout="wide")

_HERE = Path(__file__).resolve().parent
CSV = next((p for p in [_HERE / "bench_results.csv",
                        _HERE.parent / "reports" / "bench_results.csv"]
            if p.exists()),
           _HERE.parent / "reports" / "bench_results.csv")
BOARD_N = {"gen": 200, "nyt": 736}
MODE_ORDER = ["default", "no_cot", "hint_right", "hint_wrong", "interactive", "image"]
MODE_LABEL = {
    "default": "дефолт",
    "no_cot": "без CoT",
    "hint_right": "подсказка верная",
    "hint_wrong": "подсказка ложная",
    "interactive": "интерактив",
    "image": "картинка",
}
COVER_MIN, REACH_MIN = 0.95, 0.90
DIFF_ORDER = ["easy", "medium", "hard"]
DIFF_LABEL = {"easy": "лёгкие", "medium": "средние", "hard": "сложные"}

# Tableau 10 — calm classic qualitative palette.
OUTCOME_CMAP = {
    "solved": "#59a14f",     # green
    "wrong": "#f28e2b",      # orange
    "no_answer": "#79706e",  # warm gray (no real answer)
    "error": "#e15759",      # red
}
BLUE = (120, 165, 210)       # base for heat tables / bars


@st.cache_data
def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["dataset"] = df["run_file"].str.split("/").str[0]

    def outcome(v: str) -> str:
        if v.startswith("SOLVED"):
            return "solved"
        if v.startswith("WRONG"):
            return "wrong"
        if v.startswith("NO_ANSWER"):
            return "no_answer"
        return "error"

    df["outcome"] = df["verdict"].map(outcome)
    df["is_valid"] = df["outcome"].isin(["solved", "wrong"]).astype(int)
    df["is_solved"] = (df["outcome"] == "solved").astype(int)
    df["is_harness_fail"] = df["outcome"].isin(["no_answer", "error"]).astype(int)
    df["groups_correct"] = (
        df["verdict"].str.extract(r"\((\d)/4\)")[0].astype("Int64"))
    df.loc[df["outcome"] == "solved", "groups_correct"] = 4
    df["model_short"] = df["run_file"].str.split("/").str[1]
    df["board"] = (df["run_file"].str.split("/").str[-1]
                   .str.replace(".txt", "", regex=False))
    return df


@st.cache_data
def load_board_meta() -> pd.DataFrame:
    """Board difficulty tiers from boards_meta.csv (fallback: boards.jsonl).

    Only gen boards are labelled — nyt boards get NaN and drop out of the
    difficulty view. Columns: board, difficulty, trap_count, board_solve_rate.
    """
    slim = _HERE / "boards_meta.csv"
    if slim.exists():
        return pd.read_csv(slim)
    src = _HERE.parent.parent / "connections-gen" / "data" / "boards.jsonl"
    if not src.exists():
        return pd.DataFrame(
            columns=["board", "difficulty", "trap_count", "board_solve_rate"])
    rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    return pd.DataFrame([{
        "board": r["board_id"],
        "difficulty": r.get("difficulty"),
        "trap_count": r.get("trap_count"),
        "board_solve_rate": r.get("solve_rate"),
    } for r in rows])


def cell_metrics(g: pd.DataFrame) -> pd.Series:
    attempts = len(g)
    valid = int(g["is_valid"].sum())
    solved = int(g["is_solved"].sum())
    return pd.Series({
        "attempts": attempts,
        "valid": valid,
        "solved": solved,
        "reach": valid / attempts if attempts else 0.0,
        "skill": solved / valid if valid else float("nan"),
    })


def heat_bg(base):
    """Return a Styler cell fn: white->base color by value (0..100). No deps."""
    br, bgc, bb = base

    def _style(v):
        if pd.isna(v):
            return ""
        t = max(0.0, min(1.0, v / 100))
        r, g, b = (int(255 + (c - 255) * t) for c in (br, bgc, bb))
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        return f"background-color: rgb({r},{g},{b}); color: {'white' if lum < 140 else 'black'}"

    return _style


df = load(str(CSV))

# ---------------- sidebar filters ----------------
st.sidebar.header("Фильтры")
ds = st.sidebar.radio("Датасет", ["gen", "nyt", "оба"], index=0)
models = sorted(df["model_short"].unique())
sel_models = st.sidebar.multiselect("Модели", models, default=models)
modes = [m for m in MODE_ORDER if m in df["mode"].unique()]
sel_modes = st.sidebar.multiselect("Режимы", modes, default=modes)
domains = sorted(df["domain"].unique())
sel_domains = st.sidebar.multiselect("Домены", domains, default=domains)
outcomes = ["solved", "wrong", "no_answer", "error"]
sel_out = st.sidebar.multiselect("Исходы", outcomes, default=outcomes)

f = df.copy()
if ds != "оба":
    f = f[f["dataset"] == ds]
f = f[f["model_short"].isin(sel_models) & f["mode"].isin(sel_modes)
      & f["domain"].isin(sel_domains) & f["outcome"].isin(sel_out)]

st.title("Connections benchmark — explorer")
st.caption("SKILL = solved / valid (качество) · REACH = valid / attempts "
           "(здоровье пайплайна). Серым в матрице — ячейки ниже гейта "
           f"(cover ≥ {COVER_MIN:.0%}, reach ≥ {REACH_MIN:.0%}).")

tab_skill, tab_domain, tab_diff, tab_verdict, tab_mega, tab_runs = st.tabs(
    ["SKILL / REACH матрицы", "Домены", "Сложность", "Исходы",
     "Accuracy (raw)", "Сырые данные прокачек"])

# ---------------- matrices ----------------
with tab_skill:
    if f.empty:
        st.info("Нет данных под фильтры.")
    else:
        cells = (f.groupby(["dataset", "model_short", "mode"])
                 .apply(cell_metrics).reset_index())
        cells["cover"] = cells.apply(
            lambda r: r["attempts"] / BOARD_N.get(r["dataset"], r["attempts"]),
            axis=1)
        cells["reportable"] = ((cells["cover"] >= COVER_MIN)
                               & (cells["reach"] >= REACH_MIN))
        cells["skill_gated"] = cells["skill"].where(cells["reportable"])

        modes_here = [m for m in MODE_ORDER if m in set(cells["mode"])]
        skill_piv = (cells.pivot_table(index="model_short", columns="mode",
                                       values="skill_gated")
                     .reindex(columns=modes_here) * 100)
        reach_piv = (cells.pivot_table(index="model_short", columns="mode",
                                       values="reach")
                     .reindex(columns=modes_here) * 100)

        st.markdown("**SKILL** — доля решённых среди валидных ответов "
                    "(показаны только reportable-ячейки)")
        st.dataframe(skill_piv.style.format("{:.0f}%", na_rep="—")
                     .map(heat_bg(BLUE)), width="stretch")

        st.markdown("**REACH** — доля попыток, где получили оцениваемый ответ "
                    "(все ячейки)")
        st.dataframe(reach_piv.style.format("{:.0f}%", na_rep="—")
                     .map(heat_bg(BLUE)), width="stretch")

        st.markdown("**Детали ячеек** (attempts / valid / reach / skill / gate)")
        show = cells[["dataset", "model_short", "mode", "attempts", "valid",
                      "reach", "skill", "cover", "reportable"]].copy()
        show["reach"] = (show["reach"] * 100).round(0)
        show["skill"] = (show["skill"] * 100).round(0)
        show["cover"] = (show["cover"] * 100).round(0)
        st.dataframe(show, use_container_width=True, hide_index=True)

# ---------------- domain ----------------
with tab_domain:
    g = f[f["dataset"] == "gen"] if ds != "nyt" else f
    if g.empty or g["domain"].nunique() <= 1:
        st.info("Домены есть только у gen-досок. Выбери gen.")
    else:
        modes_g = [m for m in MODE_ORDER if m in set(g["mode"])]

        by_mode = (g.groupby(["domain", "mode"]).apply(cell_metrics)
                   .reset_index())
        piv = (by_mode.pivot_table(index="domain", columns="mode", values="skill")
               .reindex(columns=modes_g))
        piv = piv.loc[piv.mean(axis=1).sort_values().index] * 100
        st.markdown("**SKILL: домен × режим**")
        st.dataframe(piv.style.format("{:.0f}%", na_rep="—")
                     .map(heat_bg(BLUE)), width="stretch")

        by_model = (g.groupby(["domain", "model_short"]).apply(cell_metrics)
                    .reset_index())
        pivm = by_model.pivot_table(index="domain", columns="model_short",
                                    values="skill")
        pivm = pivm.loc[pivm.mean(axis=1).sort_values().index] * 100
        st.markdown("**SKILL: домен × модель**")
        st.dataframe(pivm.style.format("{:.0f}%", na_rep="—")
                     .map(heat_bg(BLUE)), width="stretch")
        st.caption("Домен низкий у ВСЕХ моделей → баг генерации досок, а не "
                   "сложность. Низкий лишь у части → реальная разница в силе.")

        dom = (g.groupby("domain").apply(cell_metrics).reset_index()
               .sort_values("skill"))
        st.markdown("**SKILL по доменам** (pooled по моделям)")
        fig = px.bar(dom, x="skill", y="domain", orientation="h",
                     text=dom["skill"].map(lambda x: f"{x:.0%}"),
                     hover_data=["valid", "reach"],
                     color="skill", color_continuous_scale="Blues")
        fig.update_layout(coloraxis_showscale=False, xaxis_tickformat=".0%",
                          yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

# ---------------- difficulty ----------------
with tab_diff:
    st.markdown("Тиры сложности берутся из `boards_meta.csv` (маппинг из "
                "`connections-gen/data/boards.jsonl`). Размечены только "
                "**gen**-доски, поэтому вкладка всегда про gen.")
    meta = load_board_meta()
    d = f[f["dataset"] == "gen"].merge(meta, on="board", how="inner")
    if d.empty or "difficulty" not in d.columns or d["difficulty"].isna().all():
        st.info("Нет размеченных gen-досок под фильтры. Выбери gen и сними "
                "лишние фильтры.")
    else:
        d = d[d["difficulty"].notna()]
        diffs_here = [x for x in DIFF_ORDER if x in set(d["difficulty"])]

        # SKILL: model × difficulty
        by_md = (d.groupby(["model_short", "difficulty"]).apply(cell_metrics)
                 .reset_index())
        piv = (by_md.pivot_table(index="model_short", columns="difficulty",
                                 values="skill")
               .reindex(columns=diffs_here) * 100)
        piv = piv.loc[piv.mean(axis=1).sort_values(ascending=False).index]
        piv = piv.rename(columns=DIFF_LABEL)
        st.markdown("**SKILL: модель × сложность** (solved / valid)")
        st.dataframe(piv.style.format("{:.0f}%", na_rep="—")
                     .map(heat_bg(BLUE)), width="stretch")
        st.caption("Разрыв easy→hard = насколько модель проседает на трудных "
                   "досках. Ровная строка → устойчивая к сложности модель.")

        # SKILL: difficulty × mode (pooled models)
        by_dm = (d.groupby(["difficulty", "mode"]).apply(cell_metrics)
                 .reset_index())
        modes_d = [m for m in MODE_ORDER if m in set(d["mode"])]
        pivm = (by_dm.pivot_table(index="difficulty", columns="mode",
                                  values="skill")
                .reindex(index=diffs_here, columns=modes_d) * 100)
        pivm = (pivm.rename(index=DIFF_LABEL)
                .rename(columns=MODE_LABEL))
        st.markdown("**SKILL: сложность × режим** (pooled по моделям)")
        st.dataframe(pivm.style.format("{:.0f}%", na_rep="—")
                     .map(heat_bg(BLUE)), width="stretch")

        # full breakdown: one table per difficulty, model × mode
        by_mmd = (d.groupby(["difficulty", "model_short", "mode"])
                  .apply(cell_metrics).reset_index())
        st.markdown("**Полная разбивка: модель × режим по каждой сложности** "
                    "(SKILL = solved / valid)")
        for dif in diffs_here:
            sub = by_mmd[by_mmd["difficulty"] == dif]
            tab = (sub.pivot_table(index="model_short", columns="mode",
                                   values="skill")
                   .reindex(columns=modes_d) * 100)
            tab = tab.loc[tab.mean(axis=1).sort_values(ascending=False).index]
            tab = tab.rename(columns=MODE_LABEL)
            tab.index.name = "модель"
            st.markdown(f"**{DIFF_LABEL[dif].capitalize()}**")
            st.dataframe(tab.style.format("{:.0f}%", na_rep="—")
                         .map(heat_bg(BLUE)), width="stretch")


# ---------------- verdicts ----------------
with tab_verdict:
    if f.empty:
        st.info("Нет данных под фильтры.")
    else:
        order = {"solved": 0, "wrong": 1, "no_answer": 2, "error": 3}
        cmap = OUTCOME_CMAP
        vc = (f.groupby(["mode", "model_short", "outcome"]).size()
              .reset_index(name="n"))
        vc["share"] = (vc["n"]
                       / vc.groupby(["mode", "model_short"])["n"].transform("sum")
                       * 100)
        vc["Режим"] = vc["mode"].map(MODE_LABEL)
        mode_order_ru = [MODE_LABEL[m] for m in MODE_ORDER if m in set(vc["mode"])]
        fig = px.bar(vc, x="share", y="model_short", color="outcome",
                     facet_col="Режим", orientation="h", barmode="stack",
                     category_orders={"outcome": list(order),
                                      "Режим": mode_order_ru},
                     color_discrete_map=cmap)
        fig.update_xaxes(range=[0, 100], ticksuffix="%", title="")
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        fig.update_layout(
            title="Исходы: режим × модель (доля, каждый бар = 100%)",
            yaxis_title="", legend_title="исход", height=560,
            margin=dict(l=0, r=0, t=60, b=0))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Каждый бар нормирован до 100% — сравниваем только доли исходов. "
                   "Покрытие и абсолюты — на вкладке SKILL/REACH. "
                   "no_answer + error = сбои пайплайна, не качество модели.")

# ---------------- runs drill ----------------
with tab_runs:
    st.markdown("Срез по фильтрам слева (модель / домен / режим / исход). "
                "Кликни колонку — сортировка; ссылка → сырой прогон в Drive.")
    view = f
    st.caption(f"{len(view):,} строк")
    st.dataframe(
        view[["model_short", "domain", "mode", "verdict", "groups_correct",
              "gold_answer", "model_answer", "drive_link"]].head(2000),
        use_container_width=True, hide_index=True,
        column_config={
            "drive_link": st.column_config.LinkColumn("run", display_text="открыть"),
            "gold_answer": st.column_config.TextColumn("gold", width="medium"),
            "model_answer": st.column_config.TextColumn("ответ модели", width="medium"),
        })
    if len(view) > 2000:
        st.caption("Показаны первые 2000 — сузь фильтры для полного среза.")

# ---------------- mega table ----------------
with tab_mega:
    st.markdown("**Сырая accuracy** = solved / valid по каждому срезу "
                "**модель × домен × режим**, без гейта reportable "
                "(в отличие от вкладки SKILL/REACH, где недобитые ячейки скрыты). "
                "Строка — модель+домен, столбцы — режимы. Фильтры слева применяются.")
    metric_choice = st.selectbox(
        "Метрика в ячейках",
        ["accuracy (SKILL)", "reach", "solved (шт)", "valid (шт)"], index=0)
    valmap = {"accuracy (SKILL)": "skill", "reach": "reach",
              "solved (шт)": "solved", "valid (шт)": "valid"}
    val = valmap[metric_choice]

    grp = (f.groupby(["dataset", "model_short", "domain", "mode"])
           .apply(cell_metrics).reset_index())
    wide = grp.pivot_table(index=["dataset", "model_short", "domain"],
                           columns="mode", values=val).reset_index()
    mode_cols = [m for m in MODE_ORDER if m in wide.columns]
    wide = wide[["dataset", "model_short", "domain"] + mode_cols]
    if val in ("skill", "reach"):
        wide[mode_cols] = (wide[mode_cols] * 100).round(0)
    wide = wide.rename(columns={**MODE_LABEL, "model_short": "модель",
                                "dataset": "датасет", "domain": "домен"})
    st.dataframe(wide, width="stretch", hide_index=True)
    st.download_button(
        "Скачать таблицу (CSV)",
        wide.to_csv(index=False).encode("utf-8"),
        "bench_meta_table.csv", "text/csv")
