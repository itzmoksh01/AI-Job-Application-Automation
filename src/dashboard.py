"""Streamlit dashboard: quota, trends, ledger, blockers.

Run:  streamlit run src/dashboard.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config as cfgmod  # noqa: E402
from src.store import connect, daily_stats, fetch, last_n_days  # noqa: E402

st.set_page_config(page_title="Job Application Dashboard", layout="wide")

cfg = cfgmod.load_config()
tz = ZoneInfo(cfgmod.timezone_name(cfg))
today = datetime.now(tz).date().isoformat()
db = connect(cfgmod.db_path(cfg))
limit = cfgmod.automation(cfg).get("daily_limit", 200)

st.title("🎯 Job Application Dashboard")
st.caption(f"Naukri-only automation · {today} · {cfgmod.timezone_name(cfg)}")

# ---- headline metrics
s = daily_stats(db, today)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Applied today", f"{s['applied']} / {limit}")
c1.progress(min(1.0, s["applied"] / max(1, limit)))
c2.metric("Listings evaluated", s["evaluated"])
c3.metric("Avg fit (applied)", s["avg_fit_applied"])
blocked = sum(n for st_, n in s["by_status"].items() if st_.startswith("blocked"))
c4.metric("Blocked", blocked)

# ---- 7-day trend
st.subheader("Last 7 days")
trend = last_n_days(db, 7)
df_trend = pd.DataFrame(trend).set_index("date")
st.bar_chart(df_trend[["applied"]])
st.caption("Evaluated per day: " + ", ".join(f"{r['date'][5:]}:{r['evaluated']}" for r in trend))

# ---- ledger
st.subheader("Application ledger")
col_q, col_s = st.columns([3, 1])
query = col_q.text_input("Search title / company / location", "")
statuses = ["all"] + sorted({r["status"] for r in fetch(db, limit=100000)})
status_f = col_s.selectbox("Status", statuses)
rows = fetch(db, query=query or None, limit=1000)
if status_f != "all":
    rows = [r for r in rows if r["status"] == status_f]
if rows:
    df = pd.DataFrame(rows)[["date", "job_title", "company", "location",
                             "fit_score", "status", "resume_variant", "url"]]
    st.dataframe(df, use_container_width=True, hide_index=True)
else:
    st.info("No entries yet — run the automation first.")

# ---- blockers
st.subheader("Blockers needing attention")
blockers = [r for r in fetch(db, limit=100000)
            if r["status"].startswith("blocked_") or r["status"] == "skipped_question"][:20]
if blockers:
    for b in blockers:
        with st.expander(f"[{b['status']}] {b['job_title']} @ {b['company']}"):
            st.write(b["fit_reason"])
            st.caption(b["url"])
else:
    st.success("No blockers right now.")

# ---- automation profile
with st.expander("Automation profile"):
    auto = cfgmod.automation(cfg)
    st.json({
        "platforms": cfg.get("platforms"),
        "target_roles": cfg.get("target_roles"),
        "daily_limit": auto.get("daily_limit"),
        "min_fit_score": auto.get("min_fit_score"),
        "freshness_hours": auto.get("freshness_hours"),
        "active_window": auto.get("active_window"),
        "timezone": auto.get("timezone"),
    })
