import re
import urllib.parse
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Beyblade Meta (Usage Frequency)", layout="wide")
st.title("Beyblade Meta (Usage Frequency)")

# -----------------------
# DATA SOURCE
# -----------------------
with st.sidebar:
    st.header("Data Source")
    sheet_url = st.text_input(
        "Google Sheet URL",
        value="https://docs.google.com/spreadsheets/d/1h1j87RQAdwZ2XS_dLlVQ6p768HPkRxOovWoDgKtQ1Xg/edit?usp=sharing"
    )
    sheet_name = st.text_input("Sheet tab name (exact)", value="Sheet1")
    st.caption("Sheet must be publicly viewable (Anyone with the link: Viewer).")

@st.cache_data(ttl=300)
def load_sheet_as_df(url: str, tab: str) -> pd.DataFrame:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise ValueError("Could not parse spreadsheet ID from URL.")
    sid = m.group(1)
    csv_url = f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(tab)}"
    return pd.read_csv(csv_url)

try:
    df_raw = load_sheet_as_df(sheet_url, sheet_name)
    st.success(f"Loaded {len(df_raw):,} rows from “{sheet_name}”.")
except Exception as e:
    st.error(f"Could not load sheet: {e}")
    st.stop()

# -----------------------
# NORMALIZE
# -----------------------
expected = ["Event","Date","Participants","Placement","Username","Blade","Ratchet","Bit","Assist Blade"]
df = df_raw.copy()

for c in set(expected) & set(df.columns):
    df[c] = df[c].astype(str).str.strip()

if "Participants" in df.columns:
    df["Participants"] = pd.to_numeric(df["Participants"], errors="coerce")
if "Placement" in df.columns:
    df["Placement"] = pd.to_numeric(df["Placement"], errors="coerce")

# Parse Date -> tz-naive datetime, robust to weird inputs
df["_date"] = pd.to_datetime(df.get("Date"), errors="coerce")
try:
    df["_date"] = df["_date"].dt.tz_localize(None)
except (TypeError, AttributeError):
    pass  # already tz-naive

def combo_label(row):
    blade = str(row.get("Blade", "") or "")
    rat   = str(row.get("Ratchet", "") or "")
    bit   = str(row.get("Bit", "") or "")
    parts = [p for p in [blade, rat, bit] if p != ""]
    return " ".join(parts) if parts else "(Unknown Combo)"

if set(["Blade","Ratchet","Bit"]).issubset(df.columns):
    df["_combo"] = df.apply(combo_label, axis=1)
else:
    df["_combo"] = "(Unknown Combo)"

# -----------------------
# QUERY PARAMS (cross-version safe)
# -----------------------
def _qp_dict():
    # Streamlit >=1.33: st.query_params behaves like Mapping[str, str]
    try:
        q = st.query_params
        out = {}
        for k in list(q.keys()):
            v = q[k]
            out[k] = v[0] if isinstance(v, list) else v
        return out
    except Exception:
        # Older: experimental_get_query_params -> Dict[str, List[str]]
        q = st.experimental_get_query_params()
        return {k: (v[0] if isinstance(v, list) and v else "") for k, v in q.items()}

_qp = _qp_dict()
def qp_get(name, default):
    val = _qp.get(name, default)
    return default if val is None or val == "" else str(val)

# read (with sensible defaults)
qp_mode   = qp_get("mode",   "combo").lower()
qp_finish = qp_get("finish", "top3").lower()
# Default period now 6 months
qp_period = qp_get("period", "6m").lower()

# -----------------------
# FILTER CONTROLS
# -----------------------
view_map = {
    "Top combos": ("_combo", "Combo", "combo"),
    "Blades":     ("Blade",  "Blade", "blade"),
    "Ratchets":   ("Ratchet","Ratchet","ratchet"),
    "Bits":       ("Bit",    "Bit",   "bit"),
}
finish_options = ["Only 1st", "1st - 3rd", "All"]
period_options = ["Past month", "Past 3 months", "Past 6 months"]

default_view_label = {"combo":"Top combos","blade":"Blades","ratchet":"Ratchets","bit":"Bits"}.get(qp_mode, "Top combos")
default_finish_label = {"1st":"Only 1st","top3":"1st - 3rd","all":"All"}.get(qp_finish, "1st - 3rd")
default_period_label = {"1m":"Past month","3m":"Past 3 months","6m":"Past 6 months"}.get(qp_period, "Past 6 months")

col1, col2, col3 = st.columns(3)
with col1:
    try:
        view_label = st.segmented_control("Filter: view", options=list(view_map.keys()), default=default_view_label)
    except Exception:
        view_label = st.selectbox("Filter: view", list(view_map.keys()), index=list(view_map.keys()).index(default_view_label))
with col2:
    try:
        finish_label = st.segmented_control("Filter: finishes", options=finish_options, default=default_finish_label)
    except Exception:
        finish_label = st.selectbox("Filter: finishes", finish_options, index=finish_options.index(default_finish_label))
with col3:
    try:
        period_label = st.segmented_control("Filter: date range", options=period_options, default=default_period_label)
    except Exception:
        period_label = st.selectbox("Filter: date range", period_options, index=period_options.index(default_period_label))

finish_slug = {"Only 1st":"1st","1st - 3rd":"top3","All":"all"}[finish_label]
period_slug = {"Past month":"1m","Past 3 months":"3m","Past 6 months":"6m"}[period_label]
group_col, thing_label, mode_slug = view_map[view_label]

# -----------------------
# DATE + FINISH FILTERS (tz-naive everywhere)
# -----------------------
now = pd.Timestamp.now(tz=None)  # tz-naive now
months_back = {"1m": 1, "3m": 3, "6m": 6}[period_slug]
cutoff = (now - pd.DateOffset(months=months_back)).normalize()

if df["_date"].notna().any():
    df_view = df.loc[df["_date"].notna() & (df["_date"] >= cutoff)].copy()
else:
    df_view = df.copy()

if "Placement" not in df_view.columns:
    st.error("The dataset needs a 'Placement' column for finish filters.")
    st.stop()

if finish_slug == "1st":
    df_view = df_view[df_view["Placement"] == 1]
elif finish_slug == "top3":
    df_view = df_view[df_view["Placement"].between(1, 3, inclusive="both")]
# else 'all' -> no placement filter

total_rows_view = max(len(df_view), 1)

# -----------------------
# AGGREGATE (Usage / Share)
# -----------------------
def agg_frequency(frame: pd.DataFrame, group_col: str, label_name: str) -> pd.DataFrame:
    if group_col not in frame.columns:
        return pd.DataFrame(columns=[label_name, "Usage", "Share", "Detail"])
    g = (
        frame.groupby(group_col, dropna=False).size().reset_index(name="Usage")
             .sort_values(["Usage", group_col], ascending=[False, True])
    )
    g["Share"] = (g["Usage"] / total_rows_view * 100).round(1).astype(str) + "%"
    g = g.rename(columns={group_col: label_name})

    def mk_detail_link(val: str) -> str:
        return f"?view=detail&mode={urllib.parse.quote(mode_slug)}&item={urllib.parse.quote(str(val))}&finish={finish_slug}&period={period_slug}"
    g["Detail"] = g[label_name].astype(str).map(mk_detail_link)
    return g[[label_name, "Usage", "Share", "Detail"]]

agg = agg_frequency(df_view, group_col, thing_label)

# -----------------------
# FRONT PAGE TABLE (markdown so links are clickable)
# -----------------------
def render_markdown_table(df_table: pd.DataFrame, label_name: str):
    if df_table.empty:
        st.info("No rows match the current filters.")
        return
    lines = [f"| {label_name} | Usage | Share | Detail |", "|---|---:|---:|:--|"]
    for _, r in df_table.iterrows():
        label = str(r[label_name])
        usage = int(r["Usage"])
        share = r["Share"]
        detail = r["Detail"]
        lines.append(f"| {label} | {usage} | {share} | [View]({detail}) |")
    st.markdown("\n".join(lines))

# -----------------------
# ROUTING (works with both new and old query param APIs)
# -----------------------
view_qp = qp_get("view", "home").lower()
item_qp = urllib.parse.unquote(qp_get("item", ""))

if view_qp != "detail" or not item_qp:
    st.subheader("Leaderboard")
    st.caption("Click **View** to drill in. Filters above apply to both the leaderboard and details.")
    render_markdown_table(agg, thing_label)
else:
    st.markdown(f"### Details: **{item_qp}** _(by {thing_label[:-1] if thing_label.endswith('s') else thing_label}; {finish_label}, {period_label})_")

    if mode_slug == "combo":
        mask = df_view["_combo"].astype(str).eq(item_qp)
    elif mode_slug == "blade":
        mask = df_view["Blade"].astype(str).eq(item_qp) if "Blade" in df_view.columns else pd.Series(False, index=df_view.index)
    elif mode_slug == "bit":
        mask = df_view["Bit"].astype(str).eq(item_qp) if "Bit" in df_view.columns else pd.Series(False, index=df_view.index)
    elif mode_slug == "ratchet":
        mask = df_view["Ratchet"].astype(str).eq(item_qp) if "Ratchet" in df_view.columns else pd.Series(False, index=df_view.index)
    else:
        st.error("Unknown mode.")
        st.stop()

    sub = df_view.loc[mask].copy()
    if sub.empty:
        st.info("No rows matched under the current filters.")
        st.link_button("← Back to Leaderboard", "?view=home")
    else:
        usage = len(sub)
        share = usage / total_rows_view * 100
        c1, c2 = st.columns(2)
        c1.metric("Usage", f"{usage:,}")
        c2.metric("Share", f"{share:.1f}%")
        st.link_button("← Back to Leaderboard", "?view=home")

        show_cols = [c for c in ["Event","Date","Participants","Placement","Username","Blade","Ratchet","Bit","Assist Blade"] if c in sub.columns]
        st.markdown("#### Rows that make up this stat")
        if "_date" in sub.columns:
            sub = sub.sort_values(["_date","Event"], ascending=[False, True])
        st.dataframe(sub[show_cols], use_container_width=True, hide_index=True)

# Optional raw data peek
with st.expander("See raw data"):
    st.dataframe(df_raw, use_container_width=True)
