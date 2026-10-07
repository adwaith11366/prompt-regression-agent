"""Streamlit dashboard for PromptRegression run history.
Run:  streamlit run dashboard.py
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

HISTORY = Path(__file__).parent / "history" / "runs.json"

st.set_page_config(page_title="PromptRegression Dashboard", layout="wide")
st.title("PromptRegression Agent: run history")

if not HISTORY.exists():
    st.info("No runs yet. Run `python regress.py --old ... --new ... --mock` first.")
    st.stop()

runs = json.loads(HISTORY.read_text())
df = pd.DataFrame([{
    "time": r["time"], "decision": r["decision"], "mock": r.get("mock", False),
    "old_pass": r["old_metrics"]["pass_rate"], "new_pass": r["new_metrics"]["pass_rate"],
    "old_score": r["old_metrics"]["avg_score"], "new_score": r["new_metrics"]["avg_score"],
    "old_cost": r["old_metrics"]["avg_cost"], "new_cost": r["new_metrics"]["avg_cost"],
    "old_latency": r["old_metrics"]["avg_latency"], "new_latency": r["new_metrics"]["avg_latency"],
} for r in runs]).set_index("time")

last = df.iloc[-1]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Latest decision", last["decision"])
c2.metric("Pass rate", f"{last['new_pass']:.0%}", f"{(last['new_pass'] - last['old_pass']) * 100:+.0f} pts")
c3.metric("Avg score", f"{last['new_score']:.2f}", f"{last['new_score'] - last['old_score']:+.2f}")
c4.metric("Cost / request", f"${last['new_cost']:.5f}")

left, mid, right = st.columns(3)
left.subheader("Pass rate"); left.line_chart(df[["old_pass", "new_pass"]])
mid.subheader("Cost per request ($)"); mid.line_chart(df[["old_cost", "new_cost"]])
right.subheader("Latency (s)"); right.line_chart(df[["old_latency", "new_latency"]])

st.subheader("All runs")
st.dataframe(df, use_container_width=True)
