import re
import urllib.parse
import pandas as pd
import numpy as np
import streamlit as st

st.set_page_config(page_title="Beyblade Meta (Usage Frequency)", layout="wide")
st.title("Beyblade Meta (Usage Frequency) – Fast Mode")

# -----------------------
# DATA SOURCE
# -----------------------
with st.sidebar:
    st.header("Data Source")
    SHEET_URL = st.text_input(
        "Google Sheet URL",
        value="https://docs.google.com/spreadsheets/d/1h1j87RQAdwZ2XS_dLlVQ6p768HPkRxOovWoDgKtQ1Xg/edit?usp=sharing"
    )
    MAIN_TAB = st.text_input("Main sheet tab", value="Sheet1")
    IMAGES_TAB = st.text_input("Images sheet tab (optional)", value="Images")
    st.caption("Sheets must be publicly viewable (Anyone with the link: Viewer).")

@st.cache_data(ttl=600)
def load_sheet_csv(url: str, tab: str) -> pd.DataFrame:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise ValueError("Could not parse spreadsheet ID from URL.")
    sid = m.group(1)
    csv_url = f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(tab)}"
    return pd.read_csv(csv_url)

def try_load_images(url: str, tab: str):
    try:
        df = load_sheet_csv(url, tab)
        need = {"PartType","Name","ImageURL"}
        if not need.issubset(df.columns):
            return None
        return df
    except Exception:
        return None

try:
    df_raw = load_sheet_csv(SHEET_URL, MAIN_TAB)
    st.success(f"Loaded {len(df_raw):,} rows from “{MAIN_TAB}”.")
except Exception as e:
    st.error(f"Could not load sheet: {e}")
    st.stop()

images_df = try_load_images(SHEET_URL, IMAGES_TAB)

# -----------------------
# PREPROCESS & PRE-AGGREGATE (CACHED)
# -----------------------
@st.cache_data(ttl=900)
def preprocess_and_preaggregate(df_raw: pd.DataFrame):
    expected = ["Event","Date","Participants","Placement","Username","Blade","Ratchet","Bit","Assist Blade"]
    df = df_raw.copy()

    # keep only needed columns if extras exist
    keep = [c for c in expected if c in df.columns]
    df = df[keep]

    # strip spaces
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()

    # types
    if "Participants" in df.columns:
        df["Participants"] = pd.to_numeric(df["Participants"], errors="coerce")
    if "Placement" in df.columns:
        df["Placement"] = pd.to_numeric(df["Placement"], errors="coerce")

    # parse dates (tz-naive)
    df["_date"] = pd.to_datetime(df.get("Date"), errors="coerce")
    try:
        df["_date"] = df["_date"].dt.tz_localize(None)
    except Exception:
        pass
    df["_ym"] = df["_date"].dt.to_period("M")   # monthly bucket

    # combo label
    def combo_label(row):
        blade = str(row.get("Blade", "") or "")
        rat   = str(row.get("Ratchet", "") or "")
        bit   = str(row.get("Bit", "") or "")
        parts = [p for p in (blade, rat, bit) if p]
        return " ".join(parts) if parts else "(Unknown Combo)"
    df["_combo"] = df.apply(combo_label, axis=1) if {"Blade","Ratchet","Bit"}.issubset(df.columns) else "(Unknown Combo)"

    # categories as dtype('category') for speed
    for c in ["Blade","Ratchet","Bit","_combo","Username","Event"]:
        if c in df.columns:
            df[c] = df[c].astype("category")

    # Pre-aggregate monthly counts for each thing type
    pre = {}
    for key in ["_combo","Blade","Ratchet","Bit"]:
        if key not in df.columns: 
            continue
        g = df.groupby([key, "_ym"]).agg(
            count_all=("Placement", "size"),
            count_1st=("Placement", lambda s: int(np.nansum(s == 1))),
            count_top3=("Placement", lambda s: int(np.nansum((s >= 1) & (s <= 3))))
        ).reset_index()
        pre[key] = g
    return df, pre

df, pre = preprocess_and_preaggregate(df_raw)

# Optional images lookup
blade_img = ratchet_img = bit_img = {}
if images_df is not None:
    tmp = images_df.copy()
    tmp["PartType"] = tmp["PartType"].astype(str).str.strip().str.lower()
    tmp["Name"] = tmp["Name"].astype(str).str.strip()
    tmp["ImageURL"] = tmp["ImageURL"].astype(str).str.strip()
    blade_img   = tmp[tmp["PartType"]=="blade"].set_index("Name")["ImageURL"].to_dict()
    ratchet_img = tmp[tmp["PartType"]=="ratchet"].set_index("Name")["ImageURL"].to_dict()
    bit_img     = tmp[tmp["PartType"]=="bit"].set_index("Name")["ImageURL"].to_dict()

def image_for_label(label: str, mode_slug: str):
    if not label: return None
    if mode_slug == "blade":   return blade_img.get(label)
    if mode_slug == "ratchet": return ratchet_img.get(label)
    if mode_slug == "bit":     return bit_img.get(label)
    if mode_slug == "combo":
        blade_name = str(label).split(" ")[0]
        return blade_img.get(blade_name)
    return None

# -----------------------
# QUERY PARAMS + URL SYNC
# -----------------------
OUR_KEYS = {"view","mode","finish","period","item"}

def qp_read() -> dict:
    try:
        q = st.query_params
        return {k:(v[0] if isinstance(v,list) else v) for k,v in q.items()}
    except Exception:
        q = st.experimental_get_query_params()
        return {k:(v[0] if isinstance(v,list) and v else "") for k,v in q.items()}

def qp_set(params: dict):
    cur = {k:str(v) for k,v in qp_read().items() if k in OUR_KEYS}
    tgt = {k:str(v) for k,v in params.items() if k in OUR_KEYS and v is not None}
    if cur != tgt:
        try:
            st.query_params.clear(); st.query_params.update(tgt)
        except Exception:
            st.experimental_set_query_params(**tgt)

def qp_get(name, default):
    v = qp_read().get(name, default)
    return default if v in (None, "") else str(v)

# defaults: mode=combo, finish=top3, period=6m, view=home
qp_mode   = qp_get("mode",   "combo").lower()
qp_finish = qp_get("finish", "top3").lower()
qp_period = qp_get("period", "6m").lower()
qp_view   = qp_get("view",   "home").lower()
qp_item   = urllib.parse.unquote(qp_get("item", ""))

# -----------------------
# FILTER UI
# -----------------------
view_map = {
    "Top combos": ("_combo", "Combo", "combo"),
    "Blades":     ("Blade",  "Blade", "blade"),
    "Ratchets":   ("Ratchet","Ratchet","ratchet"),
    "Bits":       ("Bit",    "Bit",   "bit"),
}
finish_options = ["Only 1st", "1st - 3rd", "All"]
period_options = ["Past month", "Past 3 months", "Past 6 months"]

default_view   = {"combo":"Top combos","blade":"Blades","ratchet":"Ratchets","bit":"Bits"}.get(qp_mode, "Top combos")
default_finish = {"1st":"Only 1st","top3":"1st - 3rd","all":"All"}.get(qp_finish, "1st - 3rd")
default_period = {"1m":"Past month","3m":"Past 3 months","6m":"Past 6 months"}.get(qp_period, "Past 6 months")

col1, col2, col3 = st.columns(3)
with col1:
    try:
        view_label = st.segmented_control("View", options=list(view_map.keys()), default=default_view)
    except Exception:
        view_label = st.selectbox("View", list(view_map.keys()), index=list(view_map.keys()).index(default_view))
with col2:
    try:
        finish_label = st.segmented_control("Finishes", options=finish_options, default=default_finish)
    except Exception:
        finish_label = st.selectbox("Finishes", finish_options, index=finish_options.index(default_finish))
with col3:
    try:
        period_label = st.segmented_control("Date range", options=period_options, default=default_period)
    except Exception:
        period_label = st.selectbox("Date range", period_options, index=period_options.index(default_period))

finish_slug = {"Only 1st":"1st","1st - 3rd":"top3","All":"all"}[finish_label]
period_slug = {"Past month":"1m","Past 3 months":"3m","Past 6 months":"6m"}[period_label]
group_col, thing_label, mode_slug = view_map[view_label]

# Perf knobs
c1, c2 = st.columns(2)
with c1:
    min_usage = st.slider("Min usage (leaderboard)", 1, 50, 1)
with c2:
    show_images = st.toggle("Show images", value=False, help="Turn on if you want thumbnails; off is faster.")

# -----------------------
# CUT-OFF MONTH (as Period) & AGG PICK
# -----------------------
months_back = {"1m":1,"3m":3,"6m":6}[period_slug]
cutoff_period = (pd.Timestamp.now(tz=None) - pd.DateOffset(months=months_back)).to_period("M")

metric_col = {"all":"count_all","1st":"count_1st","top3":"count_top3"}[finish_slug]
pre_tbl = pre.get(group_col)
if pre_tbl is None:
    st.info("No data for this view.")
    st.stop()

# Slice aggregated table by month >= cutoff, then sum Usage by label
view_slice = pre_tbl[ pre_tbl["_ym"].notna() & (pre_tbl["_ym"] >= cutoff_period) ].copy()
agg = (view_slice.groupby(group_col, as_index=False)[metric_col]
       .sum()
       .rename(columns={group_col: thing_label, metric_col: "Usage"})
      )
# Filter by min usage & sort
agg = agg[agg["Usage"] >= min_usage].sort_values(["Usage", thing_label], ascending=[False, True])

total_usage = int(agg["Usage"].sum()) or 1
agg["Share"] = (agg["Usage"] / total_usage * 100).round(1).astype(str) + "%"

# Detail link + (optional) image
def mk_detail_link(val: str) -> str:
    return f"?view=detail&mode={urllib.parse.quote(mode_slug)}&item={urllib.parse.quote(str(val))}&finish={finish_slug}&period={period_slug}"

agg["Detail"] = agg[thing_label].astype(str).map(mk_detail_link)

if show_images and images_df is not None:
    agg["Image"] = agg[thing_label].astype(str).map(lambda v: image_for_label(v, mode_slug))
    # put image first
    cols = ["Image", thing_label, "Usage", "Share", "Detail"]
    agg = agg[cols]
else:
    agg = agg[[thing_label, "Usage", "Share", "Detail"]]

# -----------------------
# ROUTING + URL SYNC
# -----------------------
effective_view = "detail" if (qp_view == "detail" and qp_item) else "home"
params_to_set = {"view": effective_view, "mode": mode_slug, "finish": finish_slug, "period": period_slug}
if effective_view == "detail": params_to_set["item"] = qp_item
qp_set(params_to_set)

# -----------------------
# RENDER
# -----------------------
if effective_view == "home":
    st.subheader("Leaderboard (pre-aggregated; instant)")
    column_config = {"Detail": st.column_config.LinkColumn("Detail", display_text="View")}
    if show_images and images_df is not None:
        column_config["Image"] = st.column_config.ImageColumn("Pic", width="small")

    st.dataframe(
        agg,
        use_container_width=True,
        hide_index=True,
        column_config=column_config
    )
else:
    st.markdown(f"### Details: **{qp_item}** _(by {thing_label[:-1] if thing_label.endswith('s') else thing_label}; {finish_label}, {period_label})_")

    # Build a fast mask using precomputed month & raw columns
    mask = df["_ym"].notna() & (df["_ym"] >= cutoff_period)
    if finish_slug == "1st":
        mask &= (df["Placement"] == 1)
    elif finish_slug == "top3":
        mask &= df["Placement"].between(1, 3, inclusive="both")

    if mode_slug == "combo":
        mask &= df["_combo"].astype(str).eq(qp_item)
    elif mode_slug == "blade" and "Blade" in df.columns:
        mask &= df["Blade"].astype(str).eq(qp_item)
    elif mode_slug == "ratchet" and "Ratchet" in df.columns:
        mask &= df["Ratchet"].astype(str).eq(qp_item)
    elif mode_slug == "bit" and "Bit" in df.columns:
        mask &= df["Bit"].astype(str).eq(qp_item)
    else:
        st.error("Unknown mode or missing column.")
        st.stop()

    sub = df.loc[mask].copy()
    if sub.empty:
        st.info("No rows matched under the current filters.")
        st.link_button("← Back to Leaderboard", "?view=home")
    else:
        usage = len(sub)
        share = (usage / total_usage * 100) if total_usage else 0
        c1, c2 = st.columns(2)
        c1.metric("Usage", f"{usage:,}")
        c2.metric("Share (of leaderboard total)", f"{share:.1f}%")
        st.link_button("← Back to Leaderboard", "?view=home")

        show_cols = [c for c in ["Event","Date","Participants","Placement","Username","Blade","Ratchet","Bit","Assist Blade"] if c in sub.columns]
        sub = sub.sort_values(["_date","Event"], ascending=[False, True])
        st.markdown("#### Rows that make up this stat")
        st.dataframe(sub[show_cols], use_container_width=True, hide_index=True)

# Optional raw data peek
with st.expander("See raw data"):
    st.dataframe(df_raw, use_container_width=True)
