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
    sheet_name = st.text_input("Main sheet tab", value="Sheet1")
    images_sheet_name = st.text_input("Images sheet tab (optional)", value="Images")
    st.caption("Sheets must be publicly viewable (Anyone with the link: Viewer).")

@st.cache_data(ttl=300)
def load_sheet_as_df(url: str, tab: str) -> pd.DataFrame:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        raise ValueError("Could not parse spreadsheet ID from URL.")
    sid = m.group(1)
    csv_url = f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={urllib.parse.quote(tab)}"
    return pd.read_csv(csv_url)

def try_load_images_sheet(url: str, tab: str) -> pd.DataFrame | None:
    try:
        df = load_sheet_as_df(url, tab)
        needed = {"PartType", "Name", "ImageURL"}
        if not needed.issubset(set(df.columns)):
            return None
        return df
    except Exception:
        return None

# Load main
try:
    df_raw = load_sheet_as_df(sheet_url, sheet_name)
    st.success(f"Loaded {len(df_raw):,} rows from “{sheet_name}”.")
except Exception as e:
    st.error(f"Could not load sheet: {e}")
    st.stop()

# Load images lookup (optional)
images_df = try_load_images_sheet(sheet_url, images_sheet_name)
if images_df is None:
    st.info("No Images tab found (expecting columns: PartType, Name, ImageURL). Image column will be hidden.")
else:
    st.success(f"Loaded {len(images_df):,} image mappings from “{images_sheet_name}”.")

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

# Parse Date -> tz-naive datetime (avoid tz-aware/naive comparison)
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
# IMAGE LOOKUPS (optional)
# -----------------------
blade_img = {}
ratchet_img = {}
bit_img = {}
if images_df is not None:
    # Normalize names for safe matching
    tmp = images_df.copy()
    tmp["PartType"] = tmp["PartType"].astype(str).str.strip()
    tmp["Name"] = tmp["Name"].astype(str).str.strip()
    tmp["ImageURL"] = tmp["ImageURL"].astype(str).str.strip()

    blade_img   = tmp[tmp["PartType"].str.lower()=="blade"].set_index("Name")["ImageURL"].to_dict()
    ratchet_img = tmp[tmp["PartType"].str.lower()=="ratchet"].set_index("Name")["ImageURL"].to_dict()
    bit_img     = tmp[tmp["PartType"].str.lower()=="bit"].set_index("Name")["ImageURL"].to_dict()

def image_for_row(name: str, mode_slug: str) -> str | None:
    """Return a small image URL for the leaderboard."""
    if not name:
        return None
    if mode_slug == "blade":
        return blade_img.get(name)
    if mode_slug == "ratchet":
        return ratchet_img.get(name)
    if mode_slug == "bit":
        return bit_img.get(name)
    if mode_slug == "combo":
        # For combo rows, show the Blade image (first token in the combo string)
        blade_name = name.split(" ")[0] if " " in name else name
        return blade_img.get(blade_name)
    return None

# -----------------------
# QUERY PARAM HELPERS (cross-version safe) + URL SYNC
# -----------------------
OUR_KEYS = {"view","mode","finish","period","item"}

def _qp_read() -> dict:
    try:
        q = st.query_params
        out = {}
        for k in list(q.keys()):
            v = q[k]
            out[k] = v[0] if isinstance(v, list) else v
        return out
    except Exception:
        q = st.experimental_get_query_params()
        return {k: (v[0] if isinstance(v, list) and v else "") for k, v in q.items()}

def _qp_set(new_params: dict):
    current = {k: str(v) for k, v in _qp_read().items() if k in OUR_KEYS}
    target  = {k: str(v) for k, v in new_params.items() if k in OUR_KEYS and v is not None}
    if current != target:
        try:
            st.query_params.clear()
            st.query_params.update(target)
        except Exception:
            st.experimental_set_query_params(**target)

def qp_get(name, default):
    val = _qp_read().get(name, default)
    return default if val is None or val == "" else str(val)

# Read initial params (defaults: mode=combo, finish=top3, period=6m, view=home)
qp_mode   = qp_get("mode",   "combo").lower()
qp_finish = qp_get("finish", "top3").lower()
qp_period = qp_get("period", "6m").lower()
qp_view   = qp_get("view",   "home").lower()
qp_item   = urllib.parse.unquote(qp_get("item", ""))

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
# AGGREGATE (Usage / Share) + IMAGE + DETAIL LINK
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

    # Image URL (optional)
    if images_df is not None:
        g["Image"] = g[label_name].astype(str).map(lambda v: image_for_row(v, mode_slug))
    else:
        g["Image"] = None

    # Detail link
    def mk_detail_link(val: str) -> str:
        return f"?view=detail&mode={urllib.parse.quote(mode_slug)}&item={urllib.parse.quote(str(val))}&finish={finish_slug}&period={period_slug}"
    g["Detail"] = g[label_name].astype(str).map(mk_detail_link)

    # Column order
    cols = [label_name, "Usage", "Share", "Detail"]
    if images_df is not None:
        cols = ["Image"] + cols
    return g[cols]

agg = agg_frequency(df_view, group_col, thing_label)

# -----------------------
# ROUTING + URL SYNC
# -----------------------
effective_view = "detail" if (qp_view == "detail" and qp_item) else "home"

# Push current filters into URL (and item if detail)
params_to_set = {
    "view": effective_view,
    "mode": mode_slug,
    "finish": finish_slug,
    "period": period_slug,
}
if effective_view == "detail":
    params_to_set["item"] = qp_item
_qp_set(params_to_set)

# -----------------------
# RENDER
# -----------------------
if effective_view == "home":
    st.subheader("Leaderboard")
    st.caption("Click **View** to drill in. Filters above apply to both the leaderboard and details.")

    column_config = {
        "Detail": st.column_config.LinkColumn("Detail", display_text="View"),
    }
    if images_df is not None:
        column_config["Image"] = st.column_config.ImageColumn(
            "Pic", help="Thumbnail", width="small"
        )

    st.dataframe(
        agg,
        use_container_width=True,
        hide_index=True,
        column_config=column_config
    )

else:
    st.markdown(f"### Details: **{qp_item}** _(by {thing_label[:-1] if thing_label.endswith('s') else thing_label}; {finish_label}, {period_label})_")

    if mode_slug == "combo":
        mask = df_view["_combo"].astype(str).eq(qp_item)
    elif mode_slug == "blade":
        mask = df_view["Blade"].astype(str).eq(qp_item) if "Blade" in df_view.columns else pd.Series(False, index=df_view.index)
    elif mode_slug == "bit":
        mask = df_view["Bit"].astype(str).eq(qp_item) if "Bit" in df_view.columns else pd.Series(False, index=df_view.index)
    elif mode_slug == "ratchet":
        mask = df_view["Ratchet"].astype(str).eq(qp_item) if "Ratchet" in df_view.columns else pd.Series(False, index=df_view.index)
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
        if "_date" in sub.columns:
            sub = sub.sort_values(["_date","Event"], ascending=[False, True])
        st.markdown("#### Rows that make up this stat")
        st.dataframe(sub[show_cols], use_container_width=True, hide_index=True)

# Optional raw data peek
with st.expander("See raw data"):
    st.dataframe(df_raw, use_container_width=True)
