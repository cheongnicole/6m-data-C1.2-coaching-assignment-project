"""
Singapore hiring benchmark for market entry

Purpose
-------
Help companies estimate:
1. Where hiring demand is concentrated
2. What salary budget they should plan for
3. Which sectors appear tighter to hire for

Run
---
    python -m streamlit run app.py
"""

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st


HERE = Path(__file__).resolve().parent
CLEAN_PATH = HERE / "SGJobData_clean.csv"
SALARY_COL = "average_salary_clean"
DATE_COLS = [
    "metadata_expiryDate",
    "metadata_newPostingDate",
    "metadata_originalPostingDate",
    "posting_month",
]

SALARY_FLOOR = 500
SALARY_CEILING = 60_000
EXPERIENCE_ORDER = ["0-1 years", "2-4 years", "5-9 years", "10+ years"]
CORE_TECH_ROLES = [
    "Software Engineering",
    "IT / Infrastructure",
    "Data / Analytics",
    "Product / Project",
    "Design",
]


st.set_page_config(
    page_title="Singapore hiring benchmark",
    page_icon=":material/query_stats:",
    layout="wide",
    initial_sidebar_state="expanded",
)


# -----------------------------
# Data loading and preparation
# -----------------------------
def group_experience(years):
    # Turn a raw number like 3 or 8 years into a simpler band that is easier
    # to compare in charts and filters.
    if pd.isna(years):
        return None
    if years <= 1:
        return "0-1 years"
    if years <= 4:
        return "2-4 years"
    if years <= 9:
        return "5-9 years"
    return "10+ years"


def normalize_bool(series):
    # Some CSV exports store True/False as text instead of real booleans.
    # This helper standardizes them into actual True/False values.
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


@st.cache_data(show_spinner="Loading cleaned jobs data...")
def load_data():
    # Read the cleaned CSV once, then let Streamlit cache it so the app reruns faster
    # when users change filters.
    try:
        df = pd.read_csv(CLEAN_PATH, parse_dates=DATE_COLS)
    except ValueError:
        df = pd.read_csv(
            CLEAN_PATH,
            parse_dates=[
                "metadata_expiryDate",
                "metadata_newPostingDate",
                "metadata_originalPostingDate",
            ],
        )

    # Convert important analysis columns into numeric values.
    # If a bad value appears, turn it into NaN instead of crashing the app.
    numeric_cols = [
        "metadata_totalNumberJobApplication",
        "numberOfVacancies",
        "metadata_totalNumberOfView",
        "metadata_repostCount",
        "salary_minimum",
        "salary_maximum",
        "average_salary",
        "average_salary_clean",
        "minimumYearsExperience",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Fall back to the original average salary if the cleaned salary column is missing.
    if SALARY_COL not in df.columns and "average_salary" in df.columns:
        df[SALARY_COL] = df["average_salary"]

    # salary_reliable tells the dashboard which rows are safe to use for pay benchmarks.
    if "salary_reliable" in df.columns:
        df["salary_reliable"] = normalize_bool(df["salary_reliable"])
    else:
        df["salary_reliable"] = df[SALARY_COL].between(SALARY_FLOOR, SALARY_CEILING)

    if "salary_min_clean" not in df.columns and "salary_minimum" in df.columns:
        df["salary_min_clean"] = df["salary_minimum"]
    if "salary_max_clean" not in df.columns and "salary_maximum" in df.columns:
        df["salary_max_clean"] = df["salary_maximum"]

    # posting_month is used in trend charts, so we create it if the CSV does not already have it.
    if "posting_month" not in df.columns:
        df["posting_month"] = (
            pd.to_datetime(df["metadata_originalPostingDate"], errors="coerce")
            .dt.to_period("M")
            .dt.to_timestamp()
        )
    else:
        df["posting_month"] = pd.to_datetime(df["posting_month"], errors="coerce")

    # Group raw years of experience into a few clearer bands for comparison.
    if "experience_group" not in df.columns and "minimumYearsExperience" in df.columns:
        df["experience_group"] = df["minimumYearsExperience"].apply(group_experience)

    # Fill missing labels so charts do not break and every row still has a bucket.
    df["main_category"] = df["main_category"].fillna("Unknown")
    df["primary_role"] = df["primary_role"].fillna("Other / Unclassified")
    df["title_clean"] = (
        df["title_clean"]
        if "title_clean" in df.columns
        else df["title"].astype(str).str.strip().str.lower()
    )

    # Replace zero vacancies or zero views with NaN before dividing, so we avoid
    # dividing by zero when building the hiring-response metrics.
    vacancies = df["numberOfVacancies"].where(df["numberOfVacancies"] > 0)
    views = df["metadata_totalNumberOfView"].where(df["metadata_totalNumberOfView"] > 0)

    # Candidate-response metrics. These are more informative than repost counts
    # for this dataset because repost values are overwhelmingly zero-heavy.
    df["applications_per_vacancy"] = df["metadata_totalNumberJobApplication"] / vacancies
    df["application_rate"] = df["metadata_totalNumberJobApplication"] / views
    df["role_classified"] = df["primary_role"].ne("Other / Unclassified")

    return df


def apply_filters(frame, filters):
    dff = frame.copy()

    # Each filter only runs when the user has actively selected something.
    if filters["categories"]:
        dff = dff[dff["main_category"].isin(filters["categories"])]
    if filters["roles"]:
        dff = dff[dff["primary_role"].isin(filters["roles"])]
    if filters["employment"]:
        dff = dff[dff["employmentTypes"].isin(filters["employment"])]
    if filters["experience"]:
        dff = dff[dff["experience_group"].isin(filters["experience"])]
    if filters["title_query"]:
        dff = dff[
            dff["title_clean"].str.contains(filters["title_query"], case=False, na=False)
        ]

    # The end date is pushed forward by one day so the final selected date is included.
    start = pd.Timestamp(filters["date_range"][0])
    end = pd.Timestamp(filters["date_range"][1]) + pd.Timedelta(days=1)
    dff = dff[dff["metadata_originalPostingDate"].between(start, end)]

    # When the salary-clean toggle is on, keep only rows marked as reliable.
    # This makes the full dashboard scope match the user's expectation, not just
    # the salary charts.
    if filters.get("salary_only"):
        dff = dff[dff["salary_reliable"]]
    return dff


# -----------------------------
# Aggregation helpers
# -----------------------------
def sector_benchmarks(frame, salary_frame, min_postings):
    # Build one benchmark row per sector so we can compare sectors fairly.
    # We combine demand, applicant response, and pay into one table.
    base = (
        frame.groupby("main_category")
        .agg(
            postings=("metadata_jobPostId", "nunique"),
            mean_applications_per_vacancy=("applications_per_vacancy", "mean"),
            mean_application_rate=("application_rate", "mean"),
        )
        .reset_index()
    )

    salary = (
        salary_frame
        .groupby("main_category")[SALARY_COL]
        .median()
        .rename("median_salary")
        .reset_index()
    )

    # Merge hiring-response metrics with salary metrics so each sector has
    # both "difficulty" and "cost" in the same table.
    bench = base.merge(salary, on="main_category", how="left")

    # Small groups are noisy, so only keep sectors with enough postings.
    bench = bench[bench["postings"] >= min_postings].copy()
    bench = bench.dropna(
        subset=["mean_applications_per_vacancy", "mean_application_rate", "median_salary"]
    )

    if bench.empty:
        return bench

    # Higher score = tighter market for employers:
    # lower candidate response + higher pay requirement.
    bench["tightness_score"] = (
        (1 - bench["mean_applications_per_vacancy"].rank(pct=True)) * 0.45
        + (1 - bench["mean_application_rate"].rank(pct=True)) * 0.35
        + bench["median_salary"].rank(pct=True) * 0.20
    )

    bench["tightness_label"] = pd.cut(
        bench["tightness_score"],
        bins=[-0.01, 0.25, 0.50, 0.75, 1.01],
        labels=["Easier", "Balanced", "Tighter", "Tightest"],
    )
    return bench.sort_values("tightness_score", ascending=False)


def role_response_benchmarks(frame, min_postings):
    # This creates a job-family table such as software, finance, or operations,
    # so we can see which role families attract fewer or more applicants.
    role = (
        frame[frame["role_classified"]]
        .groupby("primary_role")
        .agg(
            postings=("metadata_jobPostId", "nunique"),
            mean_applications_per_vacancy=("applications_per_vacancy", "mean"),
            mean_application_rate=("application_rate", "mean"),
        )
        .reset_index()
    )
    role = role[role["postings"] >= min_postings].copy()
    return role.sort_values("mean_applications_per_vacancy", ascending=True)


def role_panel_benchmarks(role_bench, selected_sector_name):
    # For the IT sector, keep the role-family panel focused on core tech roles
    # so the story matches what a tech employer would usually care about.
    if selected_sector_name == "Information Technology":
        return role_bench[role_bench["primary_role"].isin(CORE_TECH_ROLES)].copy()
    return role_bench.copy()


def seniority_response_benchmarks(frame, min_postings=25):
    # This creates one row per experience band so we can compare applicant
    # response across junior, mid-level, and senior roles.
    bench = (
        frame.groupby("experience_group")
        .agg(
            postings=("metadata_jobPostId", "nunique"),
            mean_applications_per_vacancy=("applications_per_vacancy", "mean"),
            mean_application_rate=("application_rate", "mean"),
        )
        .reset_index()
    )
    bench = bench[bench["postings"] >= min_postings].copy()
    bench["experience_group"] = pd.Categorical(
        bench["experience_group"], categories=EXPERIENCE_ORDER, ordered=True
    )
    return bench.sort_values("experience_group")


def sector_experience_heatmap_data(
    frame, selected_categories=None, top_n=8, min_postings=25
):
    # Build a sector-by-experience table so the dashboard can show where hiring
    # looks tighter across both dimensions at once.
    if frame.empty:
        return pd.DataFrame(), []

    selected_categories = selected_categories or []
    sector_counts = (
        frame.groupby("main_category")["metadata_jobPostId"]
        .nunique()
        .sort_values(ascending=False)
    )

    if selected_categories:
        peer_sectors = [
            sector
            for sector in sector_counts.index.tolist()
            if sector not in selected_categories
        ][: max(top_n - len(selected_categories), 0)]
        sector_order = selected_categories + peer_sectors
    else:
        sector_order = sector_counts.head(top_n).index.tolist()

    heatmap = (
        frame[
            frame["main_category"].isin(sector_order)
            & frame["experience_group"].notna()
        ]
        .groupby(["main_category", "experience_group"])
        .agg(
            postings=("metadata_jobPostId", "nunique"),
            mean_applications_per_vacancy=("applications_per_vacancy", "mean"),
        )
        .reset_index()
    )
    heatmap = heatmap[heatmap["postings"] >= min_postings].copy()
    if heatmap.empty:
        return heatmap, sector_order

    heatmap["experience_group"] = pd.Categorical(
        heatmap["experience_group"], categories=EXPERIENCE_ORDER, ordered=True
    )
    heatmap["main_category"] = pd.Categorical(
        heatmap["main_category"], categories=sector_order, ordered=True
    )
    heatmap["label_text"] = heatmap["mean_applications_per_vacancy"].apply(
        lambda v: compact_value_label(v, "number")
    )
    low_cutoff = heatmap["mean_applications_per_vacancy"].quantile(0.20)
    high_cutoff = heatmap["mean_applications_per_vacancy"].quantile(0.92)
    heatmap["label_color"] = heatmap["mean_applications_per_vacancy"].apply(
        lambda v: "white" if v <= low_cutoff or v >= high_cutoff else "#0f172a"
    )
    heatmap["selected_sector_group"] = heatmap["main_category"].astype(str).apply(
        lambda x: "Selected sector"
        if x in selected_categories
        else "Peer sectors"
    )
    return heatmap.sort_values(["main_category", "experience_group"]), sector_order


def role_experience_heatmap_data(
    frame, selected_sector_name, top_n=8, min_postings=25
):
    # When the user is already inside one sector, switch the heatmap to role
    # families by experience band so the pattern becomes more actionable.
    if frame.empty:
        return pd.DataFrame(), []

    role_frame = frame[frame["role_classified"]].copy()
    if selected_sector_name == "Information Technology":
        role_frame = role_frame[role_frame["primary_role"].isin(CORE_TECH_ROLES)].copy()

    role_counts = (
        role_frame.groupby("primary_role")["metadata_jobPostId"]
        .nunique()
        .sort_values(ascending=False)
    )
    role_order = role_counts.head(top_n).index.tolist()

    heatmap = (
        role_frame[
            role_frame["primary_role"].isin(role_order)
            & role_frame["experience_group"].notna()
        ]
        .groupby(["primary_role", "experience_group"])
        .agg(
            postings=("metadata_jobPostId", "nunique"),
            mean_applications_per_vacancy=("applications_per_vacancy", "mean"),
        )
        .reset_index()
    )
    heatmap = heatmap[heatmap["postings"] >= min_postings].copy()
    if heatmap.empty:
        return heatmap, role_order

    heatmap["experience_group"] = pd.Categorical(
        heatmap["experience_group"], categories=EXPERIENCE_ORDER, ordered=True
    )
    heatmap["primary_role"] = pd.Categorical(
        heatmap["primary_role"], categories=role_order, ordered=True
    )
    heatmap["label_text"] = heatmap["mean_applications_per_vacancy"].apply(
        lambda v: compact_value_label(v, "number")
    )
    low_cutoff = heatmap["mean_applications_per_vacancy"].quantile(0.20)
    high_cutoff = heatmap["mean_applications_per_vacancy"].quantile(0.92)
    heatmap["label_color"] = heatmap["mean_applications_per_vacancy"].apply(
        lambda v: "white" if v <= low_cutoff or v >= high_cutoff else "#0f172a"
    )
    heatmap["selected_sector_group"] = "Selected sector"
    return heatmap.sort_values(["primary_role", "experience_group"]), role_order


def heatmap_story_title(heatmap_data, single_sector_mode, selected_sector_name, group_col):
    if heatmap_data.empty:
        return "Where applicant depth looks thinnest"

    lowest_cell = heatmap_data.sort_values("mean_applications_per_vacancy", ascending=True).iloc[0]
    highest_cell = heatmap_data.sort_values("mean_applications_per_vacancy", ascending=False).iloc[0]

    if single_sector_mode:
        return (
            f"In {selected_sector_name}, {lowest_cell[group_col]} {lowest_cell['experience_group']} looks tightest, "
            f"while {highest_cell[group_col]} {highest_cell['experience_group']} draws the deepest applicant pool"
        )

    return (
        f"{lowest_cell[group_col]} {lowest_cell['experience_group']} looks tightest, "
        f"while {highest_cell[group_col]} {highest_cell['experience_group']} draws the deepest applicant pool"
    )


def experience_pay_story(salary_exp):
    if salary_exp.empty or len(salary_exp) < 2:
        return "Experience-band pay and demand"

    exp_data = salary_exp.dropna().copy()
    if exp_data.empty:
        return "Experience-band pay and demand"

    busiest_band = exp_data.sort_values("postings", ascending=False).iloc[0]
    ordered = exp_data.set_index("experience_group").reindex(EXPERIENCE_ORDER).dropna().reset_index()
    if len(ordered) < 2:
        return f"Most demand sits at {busiest_band['experience_group']}"

    ordered["prev_salary"] = ordered["median_salary"].shift(1)
    ordered["pay_jump"] = ordered["median_salary"] - ordered["prev_salary"]
    jumps = ordered.dropna(subset=["pay_jump"])
    if jumps.empty:
        return f"Most demand sits at {busiest_band['experience_group']}"

    biggest_jump = jumps.sort_values("pay_jump", ascending=False).iloc[0]
    prev_band = ordered.loc[ordered.index[ordered["experience_group"] == biggest_jump["experience_group"]][0] - 1, "experience_group"]
    return (
        f"Most demand sits at {busiest_band['experience_group']}, while pay jumps {money_md(biggest_jump['pay_jump'])} "
        f"from {prev_band} to {biggest_jump['experience_group']}"
    )


def money(value):
    if pd.isna(value):
        return "N/A"
    return f"S${value:,.0f}"


def money_md(value):
    if pd.isna(value):
        return "N/A"
    return f"S\\${value:,.0f}"


def pct_text(value):
    if pd.isna(value):
        return "N/A"
    return f"{value:.1%}"


def compact_value_label(value, kind="number"):
    if pd.isna(value):
        return ""
    if kind == "money":
        if abs(value) >= 1000:
            return f"S${value/1000:.1f}k"
        return f"S${value:,.0f}"
    if kind == "percent":
        return f"{value:.0%}" if abs(value) >= 0.1 else f"{value:.1%}"
    if kind == "integer":
        return f"{value:,.0f}"
    return f"{value:.2f}"


def metric_kind_from_title(title):
    title_lower = str(title).lower()
    if "salary" in title_lower or "pay" in title_lower:
        return "money"
    if "rate" in title_lower or "%" in title_lower:
        return "percent"
    if "posting" in title_lower or "vacanc" in title_lower:
        return "integer"
    return "number"


def build_scope_label(filters):
    if filters["title_query"]:
        base = f'"{filters["title_query"]}" roles'
    elif len(filters["roles"]) == 1:
        base = f'{filters["roles"][0]} roles'
    elif len(filters["categories"]) == 1:
        base = f'roles in {filters["categories"][0]}'
    else:
        base = "all roles in view"

    if filters["title_query"] and len(filters["categories"]) == 1:
        base += f' in {filters["categories"][0]}'
    elif filters["categories"] and len(filters["categories"]) > 1:
        base += f' across {len(filters["categories"])} selected sectors'

    return base


# -----------------------------
# Filter display helpers
# -----------------------------
def filters_are_active(filters, min_date, max_date):
    start, end = filters["date_range"]
    if pd.Timestamp(start).date() != min_date or pd.Timestamp(end).date() != max_date:
        return True
    return any(
        [
            filters["categories"],
            filters["roles"],
            filters["employment"],
            filters["experience"],
            filters["title_query"],
        ]
    )


def build_filter_badges(filters, min_date, max_date):
    badges = []
    for category in filters["categories"][:4]:
        badges.append(f":blue-badge[Sector: {category}]")
    if len(filters["categories"]) > 4:
        badges.append(f":blue-badge[+{len(filters['categories']) - 4} more sectors]")

    for role in filters["roles"][:3]:
        badges.append(f":green-badge[Job family: {role}]")
    if len(filters["roles"]) > 3:
        badges.append(f":green-badge[+{len(filters['roles']) - 3} more job families]")

    if filters["title_query"]:
        badges.append(f":orange-badge[Title contains: {filters['title_query']}]")

    if filters["employment"]:
        if len(filters["employment"]) == 1:
            badges.append(f":violet-badge[Employment: {filters['employment'][0]}]")
        else:
            badges.append(f":violet-badge[{len(filters['employment'])} employment types]")
    if filters["experience"]:
        if len(filters["experience"]) == 1:
            badges.append(f":green-badge[Experience: {filters['experience'][0]}]")
        else:
            badges.append(f":green-badge[{len(filters['experience'])} experience bands]")

    start, end = filters["date_range"]
    if pd.Timestamp(start).date() != min_date or pd.Timestamp(end).date() != max_date:
        badges.append(
            f":blue-badge[Dates: {pd.Timestamp(start):%b %Y} to {pd.Timestamp(end):%b %Y}]"
        )

    if not badges:
        badges.append(":blue-badge[Whole market selection]")
    return " ".join(badges)


# -----------------------------
# Chart builders and chart text
# -----------------------------
def scope_salary_change(salary_frame):
    # Reduce the selected salary rows to one monthly median series, then
    # compare the first month with the last month.
    if salary_frame.empty:
        return None
    monthly = (
        salary_frame.groupby("posting_month")[SALARY_COL]
        .median()
        .dropna()
        .sort_index()
    )
    if len(monthly) < 2:
        return None
    first_month = monthly.index[0]
    last_month = monthly.index[-1]
    first_value = monthly.iloc[0]
    last_value = monthly.iloc[-1]
    pct_change = None if first_value in [0, None] or pd.isna(first_value) else (last_value - first_value) / first_value
    return {
        "first_month": first_month,
        "last_month": last_month,
        "first_value": first_value,
        "last_value": last_value,
        "pct_change": pct_change,
    }


def benchmark_bar_chart(selected_label, selected_value, market_value, x_title, height=280):
    # A compact benchmark chart: orange bar = selected scope, dark rule =
    # wider-market benchmark. Good when we compare one slice against the market.
    max_value = max(selected_value, market_value) * 1.35 if pd.notna(selected_value) and pd.notna(market_value) else 1
    value_kind = metric_kind_from_title(x_title)
    benchmark_df = pd.DataFrame(
        {
            "group": ["Benchmark"],
            "selected_value": [selected_value],
            "market_value": [market_value],
            "selected_label": [selected_label],
        }
    )
    selected_text = pd.DataFrame(
        {
            "group": ["Benchmark"],
            "value": [selected_value],
            "text": [compact_value_label(selected_value, value_kind)],
        }
    )
    market_text = pd.DataFrame(
        {
            "group": ["Benchmark"],
            "value": [market_value],
            "text": [compact_value_label(market_value, value_kind)],
        }
    )
    return (
        alt.layer(
            alt.Chart(benchmark_df)
            .mark_bar(color="#c2410c", cornerRadiusEnd=6, size=28)
            .encode(
                x=alt.X(
                    "selected_value:Q",
                    title=x_title,
                    scale=alt.Scale(domain=[0, max_value]),
                ),
                y=alt.Y("group:N", title=None, axis=None),
                tooltip=[
                    alt.Tooltip("selected_label:N", title="Selected scope"),
                    alt.Tooltip("selected_value:Q", title=x_title),
                    alt.Tooltip("market_value:Q", title="Wider market"),
                ],
            ),
            alt.Chart(benchmark_df)
            .mark_rule(color="#0f172a", strokeWidth=3)
            .encode(x="market_value:Q"),
            alt.Chart(selected_text)
            .mark_text(align="left", dx=6, dy=-18, fontSize=11, color="#9a3412")
            .encode(x="value:Q", y=alt.Y("group:N", title=None, axis=None), text="text:N"),
            alt.Chart(market_text)
            .mark_text(align="center", dy=22, fontSize=11, color="#334155")
            .encode(x="value:Q", y=alt.Y("group:N", title=None, axis=None), text="text:N"),
        ).properties(height=height)
    )


def benchmark_title(selected_label, selected_value, market_value, metric_name, higher_is_harder=True, money_metric=False):
    if pd.isna(selected_value) or pd.isna(market_value):
        return f"{selected_label} compared with the whole market"
    diff = selected_value - market_value
    if market_value == 0:
        pct = None
    else:
        pct = diff / market_value

    if money_metric:
        if pct is None:
            return f"{selected_label} pays {money_md(selected_value)}"
        direction = "above" if diff > 0 else "below"
        return f"{selected_label} pays {abs(pct):.1%} {direction} the whole-market median"

    if metric_name == "applications_per_vacancy":
        if higher_is_harder:
            return f"{selected_label} gets {selected_value:.2f} applications per vacancy vs {market_value:.2f} market-wide"
        if selected_value < market_value:
            return f"{selected_label} attracts fewer applicants per vacancy than the whole market"
        return f"{selected_label} attracts more applicants per vacancy than the whole market"

    if metric_name == "application_rate":
        return f"{selected_label} converts {selected_value:.1%} of views into applications vs {market_value:.1%} market-wide"

    return f"{selected_label} compared with the whole market"


def share_donut_chart(selected_count, market_count, selected_label):
    other_count = max(market_count - selected_count, 0)
    share_df = pd.DataFrame(
        {
            "segment": [selected_label, "Rest of market"],
            "count": [selected_count, other_count],
        }
    )
    share_df["color_group"] = share_df["segment"].apply(
        lambda x: "Selected" if x == selected_label else "Other"
    )
    return (
        alt.Chart(share_df)
        .mark_arc(innerRadius=70, outerRadius=110)
        .encode(
            theta=alt.Theta("count:Q"),
            color=alt.Color(
                "color_group:N",
                scale=alt.Scale(
                    domain=["Selected", "Other"],
                    range=["#0f766e", "#e2e8f0"],
                ),
                legend=None,
            ),
            tooltip=["segment", "count"],
        )
        .properties(height=260)
    )


def experience_mix_comparison(selected_df, market_df):
    selected = (
        selected_df.groupby("experience_group")["metadata_jobPostId"]
        .nunique()
        .reindex(EXPERIENCE_ORDER)
        .fillna(0)
    )
    market = (
        market_df.groupby("experience_group")["metadata_jobPostId"]
        .nunique()
        .reindex(EXPERIENCE_ORDER)
        .fillna(0)
    )
    if selected.sum() == 0 or market.sum() == 0:
        return pd.DataFrame(columns=["experience_group", "share", "scope"])
    result = pd.DataFrame(
        {
            "experience_group": EXPERIENCE_ORDER * 2,
            "share": list((selected / selected.sum()).values) + list((market / market.sum()).values),
            "scope": ["Selected scope"] * len(EXPERIENCE_ORDER) + ["Whole market"] * len(EXPERIENCE_ORDER),
        }
    )
    return result


def experience_mix_title(comparison_df):
    if comparison_df.empty:
        return "Experience mix compared with the whole market"
    wide = comparison_df.pivot(index="experience_group", columns="scope", values="share").fillna(0)
    wide["diff"] = wide["Selected scope"] - wide["Whole market"]
    top_band = wide["diff"].abs().idxmax()
    diff = wide.loc[top_band, "diff"]
    selected_share = wide.loc[top_band, "Selected scope"]
    market_share = wide.loc[top_band, "Whole market"]
    if abs(diff) < 0.03:
        return "Experience mix is close to the whole market"
    if diff > 0:
        return f"{top_band} roles make up {selected_share:.0%} of selected postings vs {market_share:.0%} market-wide"
    return f"{top_band} roles make up {selected_share:.0%} of selected postings vs {market_share:.0%} market-wide"


def experience_mix_dumbbell_chart(comparison_df):
    if comparison_df.empty:
        return alt.Chart(pd.DataFrame({"x": [], "y": []})).mark_point()

    wide = (
        comparison_df.pivot(index="experience_group", columns="scope", values="share")
        .reindex(EXPERIENCE_ORDER)
        .reset_index()
    )
    rule_df = wide.rename(
        columns={"Selected scope": "selected_share", "Whole market": "market_share"}
    )
    point_df = comparison_df.copy()
    point_df["label_text"] = point_df["share"].apply(lambda v: compact_value_label(v, "percent"))

    return alt.layer(
        alt.Chart(rule_df)
        .mark_rule(strokeWidth=3, color="#cbd5e1")
        .encode(
            y=alt.Y("experience_group:N", sort=EXPERIENCE_ORDER, title=None),
            x=alt.X("market_share:Q", title="Share of postings", axis=alt.Axis(format="%")),
            x2="selected_share:Q",
        ),
        alt.Chart(point_df)
        .mark_circle(size=180)
        .encode(
            y=alt.Y("experience_group:N", sort=EXPERIENCE_ORDER, title=None),
            x=alt.X("share:Q", title="Share of postings", axis=alt.Axis(format="%")),
            color=alt.Color(
                "scope:N",
                scale=alt.Scale(
                    domain=["Selected scope", "Whole market"],
                    range=["#c2410c", "#94a3b8"],
                ),
                legend=alt.Legend(title=None),
            ),
            tooltip=["experience_group", "scope", alt.Tooltip("share:Q", format=".1%")],
        ),
        alt.Chart(point_df)
        .mark_text(dx=10, fontSize=11, color="#334155")
        .encode(
            y=alt.Y("experience_group:N", sort=EXPERIENCE_ORDER, title=None),
            x=alt.X("share:Q", title="Share of postings", axis=alt.Axis(format="%")),
            detail="scope:N",
            text="label_text:N",
        ),
    ).properties(height=320)


def sector_focus_text(filtered_df):
    sector_counts = (
        filtered_df.groupby("main_category")["metadata_jobPostId"]
        .nunique()
        .sort_values(ascending=False)
    )
    if sector_counts.empty:
        return "N/A", 0.0
    top_sector = sector_counts.index[0]
    top_share = sector_counts.iloc[0] / sector_counts.sum()
    return top_sector, top_share


def job_family_focus_text(filtered_df):
    role_counts = (
        filtered_df[filtered_df["role_classified"]]
        .groupby("primary_role")["metadata_jobPostId"]
        .nunique()
        .sort_values(ascending=False)
    )
    if role_counts.empty:
        return "N/A", 0.0
    top_role = role_counts.index[0]
    top_share = role_counts.iloc[0] / role_counts.sum()
    return top_role, top_share


def response_vs_market_label(mean_apv, mean_rate, market_apv, market_rate):
    if mean_apv >= market_apv * 1.15 and mean_rate >= market_rate * 1.10:
        return "stronger than the wider market"
    if mean_apv <= market_apv * 0.85 and mean_rate <= market_rate * 0.90:
        return "weaker than the wider market"
    return "broadly in line with the wider market"


def selected_sector_title(row, bench):
    salary_mid = bench["median_salary"].median()
    apv_mid = bench["mean_applications_per_vacancy"].median()
    rate_mid = bench["mean_application_rate"].median()

    if row["median_salary"] >= salary_mid and row["mean_applications_per_vacancy"] < apv_mid:
        return f'{row["main_category"]} pays above peer median but draws fewer applicants'
    if row["median_salary"] < salary_mid and row["mean_applications_per_vacancy"] < apv_mid:
        return f'{row["main_category"]} draws fewer applicants even at lower pay'
    if row["median_salary"] >= salary_mid and row["mean_application_rate"] >= rate_mid:
        return f'{row["main_category"]} pays above peer median and still attracts interest'
    return f'{row["main_category"]} sits in the easier half of the current comparison set'


def ordinal_text(n):
    if pd.isna(n):
        return "N/A"
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def peer_sector_comparison_chart(
    market_bench,
    selected_label,
    selected_value,
    metric_col,
    metric_title,
    highlight_sector=None,
    top_n=8,
    ascending=False,
):
    # Compare the selected sector with the biggest peer sectors, while the
    # dashed rule shows the median sector benchmark across the whole market.
    if market_bench.empty or pd.isna(selected_value):
        return alt.Chart(pd.DataFrame({"x": [], "y": []})).mark_point()

    # Start with the largest sectors so the comparison stays readable.
    chart_data = market_bench.sort_values("postings", ascending=False).head(top_n).copy()
    chart_data["label"] = chart_data["main_category"]
    chart_data["group"] = "Peer sectors"

    if highlight_sector and highlight_sector in market_bench["main_category"].values:
        selected_row = market_bench[market_bench["main_category"] == highlight_sector].copy()
        selected_row["label"] = selected_row["main_category"]
        selected_row["group"] = "Selected scope"
        chart_data = pd.concat([chart_data, selected_row], ignore_index=True)
        chart_data = chart_data.drop_duplicates(subset=["label"], keep="last")
    else:
        selected_row = pd.DataFrame(
            {
                "main_category": [selected_label],
                "label": [selected_label],
                metric_col: [selected_value],
                "group": ["Selected scope"],
                "postings": [pd.NA],
            }
        )
        chart_data = pd.concat([chart_data, selected_row], ignore_index=True)
        chart_data = chart_data.drop_duplicates(subset=["label"], keep="last")

    chart_data = chart_data.sort_values(metric_col, ascending=ascending).copy()
    sort_order = chart_data["label"].tolist()
    benchmark_value = market_bench[metric_col].median()
    benchmark_df = pd.DataFrame({"benchmark": [benchmark_value]})
    chart_data["label_text"] = chart_data[metric_col].apply(
        lambda v: compact_value_label(v, metric_kind_from_title(metric_title))
    )

    return alt.layer(
        alt.Chart(benchmark_df)
        .mark_rule(strokeDash=[6, 4], color="#0f172a")
        .encode(x=alt.X("benchmark:Q", title=metric_title)),
        alt.Chart(chart_data)
        .mark_circle(size=170)
        .encode(
            x=alt.X(f"{metric_col}:Q", title=metric_title),
            y=alt.Y("label:N", sort=sort_order, title=None),
            color=alt.Color(
                "group:N",
                scale=alt.Scale(
                    domain=["Selected scope", "Peer sectors"],
                    range=["#c2410c", "#cbd5e1"],
                ),
                legend=alt.Legend(title=None),
            ),
            tooltip=[
                alt.Tooltip("label:N", title="Sector"),
                alt.Tooltip(f"{metric_col}:Q", title=metric_title),
                alt.Tooltip("postings:Q", title="Postings"),
            ],
        ),
        alt.Chart(chart_data)
        .mark_text(align="left", dx=10, fontSize=11, color="#334155")
        .encode(
            x=alt.X(f"{metric_col}:Q", title=metric_title),
            y=alt.Y("label:N", sort=sort_order, title=None),
            text="label_text:N",
        ),
    ).properties(height=320)


def sector_experience_heatmap_chart(
    heatmap_data, group_order, title_text, group_col="main_category", group_title="Sector"
):
    if heatmap_data.empty:
        return alt.Chart(pd.DataFrame({"x": [], "y": []})).mark_point()

    base = alt.Chart(heatmap_data).encode(
        x=alt.X(
            "experience_group:N",
            sort=EXPERIENCE_ORDER,
            title="Experience band",
        ),
        y=alt.Y(
            f"{group_col}:N",
            sort=group_order,
            title=None,
        ),
        tooltip=[
            alt.Tooltip(f"{group_col}:N", title=group_title),
            alt.Tooltip("experience_group:N", title="Experience band"),
            alt.Tooltip(
                "mean_applications_per_vacancy:Q",
                title="Applications per vacancy",
                format=".2f",
            ),
            alt.Tooltip("postings:Q", title="Postings"),
        ],
    )

    cells = base.mark_rect(stroke="white", strokeWidth=1).encode(
        color=alt.Color(
            "mean_applications_per_vacancy:Q",
            title="Applications per vacancy",
            scale=alt.Scale(
                domain=[
                    heatmap_data["mean_applications_per_vacancy"].min(),
                    heatmap_data["mean_applications_per_vacancy"].max(),
                ],
                range=["#991b1b", "#f59e0b", "#f8fafc", "#93c5fd", "#1d4ed8"],
            ),
        ),
    )

    labels = base.mark_text(fontSize=11, fontWeight="bold", color="#0f172a").encode(
        text="label_text:N",
    )

    return alt.layer(cells, labels).properties(height=360, title=title_text)


def scope_tightness_score(selected_salary, selected_apv, selected_rate, market_bench):
    # Convert the selected scope into percentile-like scores relative to the
    # sector benchmark table, then combine them into one overall difficulty score.
    if market_bench.empty:
        return None
    if any(pd.isna(v) for v in [selected_salary, selected_apv, selected_rate]):
        return None

    pay_pct = (market_bench["median_salary"] <= selected_salary).mean()
    apv_pct = (market_bench["mean_applications_per_vacancy"] <= selected_apv).mean()
    rate_pct = (market_bench["mean_application_rate"] <= selected_rate).mean()

    return (1 - apv_pct) * 0.45 + (1 - rate_pct) * 0.35 + pay_pct * 0.20


def posting_trend_story(monthly_df, label):
    if monthly_df.empty or monthly_df["postings"].sum() == 0:
        return f"{label} posting trend"

    monthly_df = monthly_df.sort_values("posting_month").copy()
    latest_month = monthly_df["posting_month"].iloc[-1]
    latest_value = monthly_df["postings"].iloc[-1]
    year_ago_month = latest_month - pd.DateOffset(years=1)
    year_ago_match = monthly_df[monthly_df["posting_month"] == year_ago_month]

    if not year_ago_match.empty and year_ago_match["postings"].iloc[0] > 0:
        year_ago_value = year_ago_match["postings"].iloc[0]
        pct_change = (latest_value - year_ago_value) / year_ago_value
        direction = "above" if pct_change >= 0 else "below"
        return (
            f"{label} postings in {latest_month:%b %Y} are {abs(pct_change):.0%} "
            f"{direction} {year_ago_month:%b %Y}"
        )

    first_month = monthly_df["posting_month"].iloc[0]
    first_value = monthly_df["postings"].iloc[0]
    if first_value > 0:
        pct_change = (latest_value - first_value) / first_value
        direction = "above" if pct_change >= 0 else "below"
        return (
            f"{label} postings in {latest_month:%b %Y} are {abs(pct_change):.0%} "
            f"{direction} {first_month:%b %Y}"
        )

    return f"{label} posting trend"


def build_peer_trend_data(market_view_df, filtered_df, selected_label, selected_categories, top_n=8):
    # Build a time-series table containing:
    # 1. the selected scope
    # 2. the biggest peer sectors in the same date window
    # This lets one line chart compare the selected market against peers.
    peer_counts = (
        market_view_df.groupby("main_category")["metadata_jobPostId"]
        .nunique()
        .sort_values(ascending=False)
    )
    peer_names = [name for name in peer_counts.index if name not in selected_categories][:top_n]

    selected_monthly = (
        filtered_df.groupby("posting_month")["metadata_jobPostId"]
        .nunique()
        .reset_index(name="postings")
    )
    selected_monthly["series_label"] = selected_label
    selected_monthly["highlight_group"] = "Selected scope"

    peer_monthly = (
        market_view_df[market_view_df["main_category"].isin(peer_names)]
        .groupby(["posting_month", "main_category"])["metadata_jobPostId"]
        .nunique()
        .reset_index(name="postings")
        .rename(columns={"main_category": "series_label"})
    )
    peer_monthly["highlight_group"] = "Peer sectors"

    combined = pd.concat([selected_monthly, peer_monthly], ignore_index=True)
    return combined.sort_values(["highlight_group", "series_label", "posting_month"])


def build_sector_salary_trend_data(
    market_salary_frame,
    filtered_salary_frame,
    selected_label,
    selected_categories,
    experience_band,
    top_n=8,
):
    # Build a salary trend table for one experience band at a time.
    # This is used when the user wants an apples-to-apples comparison such as:
    # "Accounting 2-4 years" versus peer sectors' "2-4 years" pay.
    band_market = market_salary_frame[
        market_salary_frame["experience_group"] == experience_band
    ].copy()
    band_selected = filtered_salary_frame[
        filtered_salary_frame["experience_group"] == experience_band
    ].copy()

    peer_counts = (
        band_market.groupby("main_category")["metadata_jobPostId"]
        .nunique()
        .sort_values(ascending=False)
    )
    peer_names = [name for name in peer_counts.index if name not in selected_categories][:top_n]

    selected_monthly = (
        band_selected.groupby("posting_month")[SALARY_COL]
        .median()
        .reset_index(name="median_salary")
    )
    selected_monthly["series_label"] = selected_label
    selected_monthly["highlight_group"] = "Selected scope"

    peer_monthly = (
        band_market[band_market["main_category"].isin(peer_names)]
        .groupby(["posting_month", "main_category"])[SALARY_COL]
        .median()
        .reset_index(name="median_salary")
        .rename(columns={"main_category": "series_label"})
    )
    peer_monthly["highlight_group"] = "Peer sectors"

    # Also add a whole-market median line for the same experience band so the
    # selected sector can be compared with a stable benchmark.
    median_monthly = (
        band_market.groupby("posting_month")[SALARY_COL]
        .median()
        .reset_index(name="median_salary")
    )
    median_monthly["series_label"] = "Sector median"
    median_monthly["highlight_group"] = "Median benchmark"

    combined = pd.concat([selected_monthly, peer_monthly, median_monthly], ignore_index=True)
    combined["line_size"] = combined["highlight_group"].map(
        {"Selected scope": 4, "Median benchmark": 3, "Peer sectors": 2}
    )
    combined["line_opacity"] = combined["highlight_group"].map(
        {"Selected scope": 1.0, "Median benchmark": 0.9, "Peer sectors": 0.65}
    )
    return combined.sort_values(["highlight_group", "series_label", "posting_month"])


def band_salary_changes(salary_trend):
    trend = salary_trend.dropna().copy()
    if trend.empty:
        return []

    changes = []
    for band, band_df in trend.groupby("experience_group"):
        band_df = band_df.sort_values("posting_month")
        if len(band_df) < 2:
            continue
        first = band_df["median_salary"].iloc[0]
        last = band_df["median_salary"].iloc[-1]
        if pd.notna(first) and first > 0 and pd.notna(last):
            pct_change = (last - first) / first
            changes.append(
                {
                    "band": band,
                    "pct_change": pct_change,
                    "first": first,
                    "last": last,
                }
            )
    return changes


def salary_trend_story(salary_change, salary_trend):
    changes = band_salary_changes(salary_trend)
    if salary_change is None:
        return (
            "Salary trend by experience band",
            "No salary trend is available for this view.",
        )

    overall_change = salary_change["pct_change"]
    if overall_change is None:
        return (
            "Salary trend by experience band",
            "Overall salary movement could not be calculated for this view.",
        )

    if not changes:
        return (
            f"Overall median pay changed {overall_change:+.1%} across the selected period",
            f"Across the selected period, overall median pay moved from **{money_md(salary_change['first_value'])}** in **{salary_change['first_month']:%b %Y}** to **{money_md(salary_change['last_value'])}** in **{salary_change['last_month']:%b %Y}**, a change of **{overall_change:+.1%}**.",
        )

    biggest = max(changes, key=lambda x: abs(x["pct_change"]))
    if abs(overall_change) < 0.05:
        return (
            "Overall pay is broadly flat across the selected period",
            f"Overall median pay moved from **{money_md(salary_change['first_value'])}** in **{salary_change['first_month']:%b %Y}** to **{money_md(salary_change['last_value'])}** in **{salary_change['last_month']:%b %Y}** (**{overall_change:+.1%}**). The largest band-level move was **{biggest['band']}**, which changed from **{money_md(biggest['first'])}** to **{money_md(biggest['last'])}** (**{biggest['pct_change']:+.1%}**).",
        )

    direction = "rose" if overall_change > 0 else "fell"
    return (
        f"Overall median pay {direction} {abs(overall_change):.1%} across the selected period",
        f"Overall median pay moved from **{money_md(salary_change['first_value'])}** in **{salary_change['first_month']:%b %Y}** to **{money_md(salary_change['last_value'])}** in **{salary_change['last_month']:%b %Y}** (**{overall_change:+.1%}**). The largest band-level move was **{biggest['band']}**, from **{money_md(biggest['first'])}** to **{money_md(biggest['last'])}** (**{biggest['pct_change']:+.1%}**).",
    )


def top_highlight_chart(data, category_col, value_col, highlight_value, x_title, height=360):
    chart_data = data.copy()
    chart_data["highlight_group"] = chart_data[category_col].apply(
        lambda x: "Highlighted" if x == highlight_value else "Other"
    )
    chart_data["label_text"] = chart_data[value_col].apply(
        lambda v: compact_value_label(v, metric_kind_from_title(x_title))
    )
    return (
        alt.layer(
            alt.Chart(chart_data)
            .mark_bar()
            .encode(
                x=alt.X(f"{value_col}:Q", title=x_title),
                y=alt.Y(f"{category_col}:N", sort="-x", title=None),
                color=alt.Color(
                    "highlight_group:N",
                    scale=alt.Scale(
                        domain=["Highlighted", "Other"],
                        range=["#0f766e", "#cbd5e1"],
                    ),
                    legend=None,
                ),
                tooltip=[category_col, value_col],
            ),
            alt.Chart(chart_data)
            .mark_text(align="left", dx=6, fontSize=11, color="#334155")
            .encode(
                x=alt.X(f"{value_col}:Q", title=x_title),
                y=alt.Y(f"{category_col}:N", sort="-x", title=None),
                text="label_text:N",
            ),
        )
        .properties(height=height)
    )


def tightness_rank_chart(
    bench,
    highlight_sector=None,
    selected_score=None,
    selected_label=None,
    top_n=8,
    height=360,
):
    # Rank sectors from hardest to easiest to hire for using the precomputed
    # tightness score, and highlight the selected sector when one is chosen.
    sorted_bench = bench.sort_values("tightness_score", ascending=False).copy()
    if len(sorted_bench) <= top_n:
        chart_data = sorted_bench.copy()
    else:
        tight_count = max(3, top_n // 2)
        loose_count = max(3, top_n - tight_count)
        chart_data = pd.concat(
            [
                sorted_bench.head(tight_count),
                sorted_bench.tail(loose_count),
            ],
            ignore_index=True,
        ).drop_duplicates(subset=["main_category"], keep="first")
    if highlight_sector and highlight_sector in bench["main_category"].values:
        selected_row = bench[bench["main_category"] == highlight_sector].copy()
        chart_data = pd.concat([chart_data, selected_row], ignore_index=True)
        chart_data = chart_data.drop_duplicates(subset=["main_category"], keep="last")
    elif selected_label and selected_score is not None:
        selected_row = pd.DataFrame(
            {
                "main_category": [selected_label],
                "tightness_score": [selected_score],
                "median_salary": [pd.NA],
                "mean_applications_per_vacancy": [pd.NA],
                "mean_application_rate": [pd.NA],
                "postings": [pd.NA],
            }
        )
        chart_data = pd.concat([chart_data, selected_row], ignore_index=True)
        chart_data = chart_data.drop_duplicates(subset=["main_category"], keep="last")

    highlight_name = highlight_sector if highlight_sector else selected_label
    if highlight_name is None and not chart_data.empty:
        highlight_name = chart_data.iloc[0]["main_category"]
    chart_data = chart_data.sort_values("tightness_score", ascending=False).copy()
    chart_data["highlight_group"] = chart_data["main_category"].apply(
        lambda x: "Highlighted" if x == highlight_name else "Other"
    )
    chart_data["label_text"] = chart_data["tightness_score"].apply(
        lambda v: compact_value_label(v, "number")
    )
    return (
        alt.layer(
            alt.Chart(chart_data)
            .mark_bar(cornerRadiusEnd=5)
            .encode(
                y=alt.Y(
                    "main_category:N",
                    title=None,
                    sort=chart_data["main_category"].tolist(),
                ),
                x=alt.X(
                    "tightness_score:Q",
                    title="Hiring tightness score (higher = harder to hire)",
                ),
                color=alt.Color(
                    "highlight_group:N",
                    scale=alt.Scale(
                        domain=["Highlighted", "Other"],
                        range=["#b91c1c", "#cbd5e1"],
                    ),
                    legend=None,
                ),
                tooltip=[
                    "main_category",
                    alt.Tooltip("tightness_score:Q", format=".2f"),
                    "median_salary",
                    "mean_applications_per_vacancy",
                    "mean_application_rate",
                    "postings",
                ],
            ),
            alt.Chart(chart_data)
            .mark_text(align="left", dx=6, fontSize=11, color="#334155")
            .encode(
                y=alt.Y(
                    "main_category:N",
                    title=None,
                    sort=chart_data["main_category"].tolist(),
                ),
                x=alt.X(
                    "tightness_score:Q",
                    title="Hiring tightness score (higher = harder to hire)",
                ),
                text="label_text:N",
            ),
        )
        .properties(height=height)
    )


def salary_by_experience(frame):
    # Summarize pay by experience band so the dashboard can show how salary
    # steps up from junior to senior roles.
    result = (
        frame[frame["salary_reliable"]]
        .groupby("experience_group")[SALARY_COL]
        .agg(median_salary="median", postings="size")
        .reset_index()
    )
    result["experience_group"] = pd.Categorical(
        result["experience_group"], categories=EXPERIENCE_ORDER, ordered=True
    )
    return result.sort_values("experience_group")


def salary_trend_by_experience(frame):
    # Create monthly pay trends for each experience band so we can track
    # whether junior, mid-level, or senior pay is moving over time.
    result = (
        frame[frame["salary_reliable"]]
        .groupby(["posting_month", "experience_group"])[SALARY_COL]
        .median()
        .reset_index(name="median_salary")
    )
    result["experience_group"] = pd.Categorical(
        result["experience_group"], categories=EXPERIENCE_ORDER, ordered=True
    )
    return result.sort_values(["experience_group", "posting_month"])


def make_filters(date_range, categories, roles, employment, experience, title_query, salary_only):
    # Keep all sidebar choices in one small dictionary so downstream code reads cleanly.
    return {
        "date_range": date_range,
        "categories": categories,
        "roles": roles,
        "employment": employment,
        "experience": experience,
        "title_query": title_query.strip(),
        "salary_only": salary_only,
    }


# -----------------------------
# App state and sidebar inputs
# -----------------------------
df = load_data()

# These bounds drive the default date range in the sidebar.
min_date = df["metadata_originalPostingDate"].min().date()
max_date = df["metadata_originalPostingDate"].max().date()

category_options = sorted(df["main_category"].dropna().unique().tolist())
role_options = sorted(
    df.loc[df["role_classified"], "primary_role"].dropna().unique().tolist()
)
employment_options = sorted(df["employmentTypes"].dropna().unique().tolist())

with st.sidebar:
    st.header("Filters")

    if st.button("Reset filters"):
        st.session_state.clear()
        st.rerun()

    # These controls define the current "scope" of the market that the user wants to inspect.
    selected_dates = st.date_input(
        "Posting date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    selected_categories = st.multiselect("Sector", category_options)
    selected_roles = st.multiselect("Role family", role_options)
    selected_employment = st.multiselect("Employment type", employment_options)
    selected_experience = st.multiselect("Experience band", EXPERIENCE_ORDER)
    title_query = st.text_input("Title contains", placeholder="engineer, analyst, nurse")

    salary_only = st.checkbox(
        "Use salary-clean rows only",
        value=True,
        help=f"Limits the dashboard scope to rows between S${SALARY_FLOOR:,} and S${SALARY_CEILING:,}.",
    )
    min_n = st.slider(
        "Minimum postings per benchmark group",
        min_value=25,
        max_value=1000,
        value=100,
        step=25,
    )

# Build the current scope once, then reuse it everywhere in the dashboard.
filters = make_filters(
    selected_dates if len(selected_dates) == 2 else (min_date, max_date),
    selected_categories,
    selected_roles,
    selected_employment,
    selected_experience,
    title_query,
    salary_only,
)

filtered_df = apply_filters(df, filters)
# This wider-market view keeps the same date range but removes the narrow filters.
market_filters = make_filters(filters["date_range"], [], [], [], [], "", salary_only)
market_view_df = apply_filters(df, market_filters)

# salary_frame powers pay charts for the selected scope.
# market_salary_frame powers the "vs wider market" benchmark charts.
salary_frame = (
    filtered_df[filtered_df["salary_reliable"]].copy()
    if salary_only
    else filtered_df[filtered_df[SALARY_COL].notna()].copy()
)
market_salary_frame = (
    market_view_df[market_view_df["salary_reliable"]].copy()
    if salary_only
    else market_view_df[market_view_df[SALARY_COL].notna()].copy()
)

market_bench = sector_benchmarks(market_view_df, market_salary_frame, min_n)
role_bench = role_response_benchmarks(filtered_df, min_n)
seniority_bench = seniority_response_benchmarks(filtered_df, min_postings=25)
salary_exp = salary_by_experience(salary_frame)

# These trend tables are reused in multiple charts below.
salary_trend = salary_trend_by_experience(salary_frame)
scope_label = build_scope_label(filters)
filters_active = filters_are_active(filters, min_date, max_date)
filter_badges = build_filter_badges(filters, min_date, max_date)

st.title("Singapore hiring benchmark for market entry")
st.caption(
    "Leave sector and job-family filters blank to see the full market in the selected date range. "
    "Add a sector, job family, title, employment type, level, or experience filter to compare that slice against the wider market."
)

if filtered_df.empty:
    st.warning("No rows match the selected filters.")
    st.stop()

salary_rows = salary_frame
classified_share = filtered_df["role_classified"].mean()
selected_postings = filtered_df["metadata_jobPostId"].nunique()
market_postings_total = market_view_df["metadata_jobPostId"].nunique()
scope_share = (
    selected_postings / market_postings_total
    if market_postings_total
    else float("nan")
)
median_salary = salary_rows[SALARY_COL].median() if not salary_rows.empty else float("nan")
market_salary = market_salary_frame[SALARY_COL].median() if not market_salary_frame.empty else float("nan")
mean_apv = filtered_df["applications_per_vacancy"].mean()
market_apv = market_view_df["applications_per_vacancy"].mean()
mean_rate = filtered_df["application_rate"].mean()
market_rate = market_view_df["application_rate"].mean()

# The summary section converts the filtered rows into a few headline business metrics.
salary_label = f"S${median_salary:,.0f}" if pd.notna(median_salary) else "N/A"
lead_sector, lead_sector_share = sector_focus_text(filtered_df)
lead_job_family, lead_job_family_share = job_family_focus_text(filtered_df)
salary_change = scope_salary_change(salary_rows)
salary_trend_heading, salary_trend_caption = salary_trend_story(salary_change, salary_trend)
comparison_mode = filters_active and (
    bool(filters["title_query"])
    or len(filters["categories"]) == 1
    or len(filters["roles"]) == 1
    or len(filters["employment"]) == 1
    or len(filters["experience"]) == 1
)

# These flags help the app decide whether to tell a whole-market story or a benchmark story
# for one narrow slice such as a single sector or single experience band.
sector_filter_active = len(filters["categories"]) > 0
single_sector_mode = len(filters["categories"]) == 1
selected_sector_name = filters["categories"][0] if single_sector_mode else None

coverage_monthly = (
    filtered_df.groupby("posting_month")["metadata_jobPostId"]
    .nunique()
    .reset_index(name="postings")
    .sort_values("posting_month")
)
coverage_cutoff_date = None
if not coverage_monthly.empty:
    coverage_threshold = coverage_monthly["postings"].max() * 0.25
    coverage_match = coverage_monthly[coverage_monthly["postings"] >= coverage_threshold]
    if not coverage_match.empty:
        coverage_cutoff_date = coverage_match["posting_month"].min()

response_label = response_vs_market_label(mean_apv, mean_rate, market_apv, market_rate)
pay_delta = None if pd.isna(median_salary) or pd.isna(market_salary) or market_salary == 0 else (median_salary - market_salary) / market_salary
sector_median_apv = (
    market_bench["mean_applications_per_vacancy"].median()
    if not market_bench.empty
    else float("nan")
)
sector_median_salary = (
    market_bench["median_salary"].median()
    if not market_bench.empty
    else float("nan")
)
if single_sector_mode and selected_sector_name in market_bench["main_category"].values:
    selected_tightness_score = float(
        market_bench.loc[
            market_bench["main_category"] == selected_sector_name, "tightness_score"
        ].iloc[0]
    )
else:
    selected_tightness_score = scope_tightness_score(
        median_salary, mean_apv, mean_rate, market_bench
    )
selected_tightness_rank = (
    1 + int((market_bench["tightness_score"] > selected_tightness_score).sum())
    if not market_bench.empty and selected_tightness_score is not None
    else None
)
pay_change_text = ""
if salary_change and salary_change["pct_change"] is not None:
    direction = "up" if salary_change["pct_change"] > 0 else "down"
    pay_change_text = (
        f" Median pay is **{direction} {abs(salary_change['pct_change']):.1%}** "
        f"from **{money_md(salary_change['first_value'])}** in **{salary_change['first_month']:%b %Y}** "
        f"to **{money_md(salary_change['last_value'])}** in **{salary_change['last_month']:%b %Y}**."
    )

market_compare_text = (
    f" Median pay is **{money_md(median_salary)}** ({pay_delta:+.1%} vs the wider market in this period)."
    if pay_delta is not None
    else f" Median pay is **{money_md(median_salary)}**."
)

summary_text = (
    f"Across **{scope_label}**, hiring conditions are **{response_label}** "
    f"with **{mean_apv:.2f} applications per vacancy** versus **{market_apv:.2f}** for the wider market, "
    f"and an **{mean_rate:.1%} application rate** versus the market's **{market_rate:.1%}**. "
    f"This slice represents **{scope_share:.1%}** of captured postings in the selected period."
    f"{market_compare_text}{pay_change_text}"
)

# First show the active filters, then a plain-English summary of what this market slice means.
st.markdown(filter_badges)
st.markdown(summary_text)

with st.container(horizontal=True):
    # KPI cards give a quick business read of the selected slice before the charts.
    st.metric("Postings in scope", f"{selected_postings:,}", border=True)
    st.metric("Hiring companies", f"{filtered_df['postedCompany_name'].nunique():,}", border=True)
    st.metric(
        "Median pay",
        salary_label,
        delta=f"{median_salary - market_salary:+,.0f} vs wider market"
        if pd.notna(median_salary) and pd.notna(market_salary)
        else None,
        border=True,
    )
    st.metric(
        "Applications per vacancy",
        f"{mean_apv:.2f}",
        delta=f"{mean_apv - market_apv:+.2f} vs wider market",
        border=True,
    )
    st.metric(
        "Application rate",
        f"{mean_rate:.1%}",
        delta=f"{mean_rate - market_rate:+.1%} vs wider market",
        border=True,
    )
    st.metric(
        "Share of market",
        f"{scope_share:.0%}",
        delta=f"{selected_postings:,} of {market_postings_total:,} postings",
        border=True,
    )

with st.expander("How to read the hiring competition metrics", icon=":material/help:"):
    st.markdown(
        """
        - **Applications per vacancy** = total applications divided by the number of vacancies in each posting.
        - **Application rate** = total applications divided by total views.
        - In this dataset, **higher values mean stronger candidate response**, which usually suggests it is easier to attract applicants.
        - We do **not** use median repost count or days-open as the main hiring benchmark because repost data is too zero-heavy and posting duration is largely driven by platform rules.
        """
    )

tab1, tab2, tab3, tab4 = st.tabs(
    ["Where demand sits", "What to budget", "Where hiring looks tighter", "Filtered data"]
)

# Tab 1: demand and market coverage
with tab1:
    c1, c2 = st.columns(2)

    # Demand by sector answers: which sectors are creating the most visible hiring activity?
    demand_by_sector = (
        filtered_df.groupby("main_category")["metadata_jobPostId"]
        .nunique()
        .reset_index(name="postings")
        .sort_values("postings", ascending=False)
        .head(10)
    )

    with c1:
        if demand_by_sector.empty:
            st.subheader("Sector demand overview")
            st.caption("No sector demand view is available for the current filters.")
        elif single_sector_mode and comparison_mode:
            st.subheader(
                f"{lead_sector} represents {scope_share:.1%} of captured hiring demand in this period"
            )
            sector_share_chart = share_donut_chart(
                selected_postings,
                market_postings_total,
                lead_sector,
            )
            st.altair_chart(sector_share_chart)
            st.caption(
                f"The selected sector contributes **{selected_postings:,} postings** out of **{market_postings_total:,}** captured across the wider market in the same date window."
            )
        else:
            demand_leader = demand_by_sector.iloc[0]
            if lead_sector_share >= 0.60:
                st.subheader(
                    f"{scope_label.capitalize()} are concentrated in {demand_leader['main_category']}"
                )
            else:
                st.subheader(
                    f"{demand_leader['main_category']} leads demand in the current scope"
                )
            sector_chart = top_highlight_chart(
                demand_by_sector,
                "main_category",
                "postings",
                demand_leader["main_category"],
                "Unique postings",
            )
            st.altair_chart(sector_chart)
            st.caption(
                f"The highlighted sector accounts for **{lead_sector_share:.0%}** of postings in the current scope."
            )

    demand_over_time = (
        filtered_df.groupby("posting_month")["metadata_jobPostId"]
        .nunique()
        .reset_index(name="postings")
        .sort_values("posting_month")
    )

    with c2:
        # The time-series view shows whether demand is growing, stable, or falling over time.
        if sector_filter_active:
            st.subheader(posting_trend_story(demand_over_time, scope_label.capitalize()))
            peer_trend_data = build_peer_trend_data(
                market_view_df,
                filtered_df,
                scope_label,
                filters["categories"],
                top_n=8,
            )
            series_order = [scope_label] + [
                name for name in peer_trend_data["series_label"].unique().tolist()
                if name != scope_label
            ]
            series_colors = [
                "#c2410c",
                "#0f766e",
                "#1d4ed8",
                "#7c3aed",
                "#475569",
                "#0284c7",
                "#9333ea",
                "#059669",
                "#64748b",
            ][: len(series_order)]
            trend_chart = (
                alt.Chart(peer_trend_data)
                .mark_line(point=True)
                .encode(
                    x=alt.X("posting_month:T", title="Posting month"),
                    y=alt.Y("postings:Q", title="Unique postings"),
                    detail="series_label:N",
                    color=alt.Color(
                        "series_label:N",
                        scale=alt.Scale(
                            domain=series_order,
                            range=series_colors,
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    size=alt.condition(
                        alt.datum.highlight_group == "Selected scope",
                        alt.value(4),
                        alt.value(2),
                    ),
                    opacity=alt.condition(
                        alt.datum.highlight_group == "Selected scope",
                        alt.value(1.0),
                        alt.value(0.72),
                    ),
                    tooltip=["posting_month:T", "series_label:N", "postings:Q"],
                )
            )
        else:
            st.subheader(posting_trend_story(demand_over_time, scope_label.capitalize()))
            trend_base = alt.Chart(demand_over_time).encode(
                x=alt.X("posting_month:T", title="Posting month"),
                y=alt.Y("postings:Q", title="Unique postings"),
                tooltip=["posting_month:T", "postings"],
            )
            trend_chart = trend_base.mark_line(point=True, color="#0f766e")
        layers = []
        if coverage_cutoff_date is not None:
            shaded = alt.Chart(
                pd.DataFrame(
                    {
                        "start": [demand_over_time["posting_month"].min()],
                        "end": [coverage_cutoff_date],
                    }
                )
            ).mark_rect(color="#e2e8f0", opacity=0.65).encode(
                x="start:T",
                x2="end:T",
            )
            layers.append(shaded)
        layers.append(trend_chart)
        st.altair_chart(alt.layer(*layers).properties(height=360))
        if coverage_cutoff_date is not None:
            if sector_filter_active:
                st.caption(
                    f"The orange line is the selected scope and the other lines are the biggest peer sectors. Months before **{coverage_cutoff_date:%B %Y}** are shaded because posting volume is much lower and likely reflects weaker dataset collection coverage."
                )
            else:
                st.caption(
                    f"Months before **{coverage_cutoff_date:%B %Y}** are shaded because posting volume is much lower and likely reflects weaker dataset collection coverage."
                )
        else:
            if sector_filter_active:
                st.caption(
                    "The orange line is the selected scope and the other lines are the biggest peer sectors. Read this directionally: it shows the hiring volume captured by this dataset, not the full economy."
                )
            else:
                st.caption(
                    "Read this directionally: it shows the hiring volume captured by this dataset, not the full economy."
                )

    d1, d2 = st.columns(2)

    experience_mix = (
        filtered_df.groupby("experience_group")["metadata_jobPostId"]
        .nunique()
        .reindex(EXPERIENCE_ORDER)
        .reset_index(name="postings")
    )
    experience_compare = experience_mix_comparison(filtered_df, market_view_df)

    with d1:
        if filters_active:
            st.subheader(experience_mix_title(experience_compare))
            exp_chart = experience_mix_dumbbell_chart(experience_compare)
            st.altair_chart(exp_chart)
            st.caption(
                "This shows whether the selected hiring scope is more junior or more senior than the whole market."
            )
        else:
            st.subheader("Mid-level experience bands carry most of the market's hiring volume")
            exp_chart = (
                alt.layer(
                    alt.Chart(experience_mix.dropna())
                    .mark_bar(color="#7c3aed")
                    .encode(
                        x=alt.X("experience_group:N", sort=EXPERIENCE_ORDER, title="Experience band"),
                        y=alt.Y("postings:Q", title="Unique postings"),
                        tooltip=["experience_group", "postings"],
                    ),
                    alt.Chart(experience_mix.dropna())
                    .mark_text(dy=-8, fontSize=11, color="#4c1d95")
                    .encode(
                        x=alt.X("experience_group:N", sort=EXPERIENCE_ORDER, title="Experience band"),
                        y=alt.Y("postings:Q", title="Unique postings"),
                        text=alt.Text("postings:Q", format=",.0f"),
                    ),
                )
                .properties(height=320)
            )
            st.altair_chart(exp_chart)
            st.caption("This helps show where overall demand is concentrated across experience bands.")

    role_mix = (
        filtered_df[filtered_df["role_classified"]]
        .groupby("primary_role")["metadata_jobPostId"]
        .nunique()
        .reset_index(name="postings")
        .sort_values("postings", ascending=False)
        .head(10)
    )

    with d2:
        if role_mix.empty:
            st.subheader("Job-family breakdown in the current scope")
        elif lead_job_family_share >= 0.60:
            st.subheader(
                f"{lead_job_family} dominates the job-family mix in this view"
            )
        else:
            st.subheader("Job-family demand is spread across several role types")
        if role_mix.empty:
            st.caption("No job-family view is available in the current filters.")
        else:
            role_leader = role_mix.iloc[0]
            role_chart = top_highlight_chart(
                role_mix,
                "primary_role",
                "postings",
                role_leader["primary_role"],
                "Unique postings",
                height=320,
            )
            st.altair_chart(role_chart)
            st.caption(
                f"Here, **sector** means the employer's industry, while **job family** means the type of role being hired, such as software, finance, or operations. "
                f"About **{classified_share:.0%}** of postings in this view have a usable job-family label, and **{role_leader['primary_role']}** makes up **{lead_job_family_share:.0%}** of the classified mix."
            )

# Tab 2: salary benchmarks and pay trends
with tab2:
    s1, s2 = st.columns(2)

    # If one experience band is already selected in the sidebar, keep the pay comparison
    # locked to that same band so the comparison stays apples-to-apples.
    if filters_active:
        pay_experience_options = ["All experience bands"] + [
            band
            for band in EXPERIENCE_ORDER
            if band in market_salary_frame["experience_group"].dropna().unique().tolist()
        ]
        if len(filters["experience"]) == 1:
            selected_pay_experience = filters["experience"][0]
            st.caption(f"Pay comparison is locked to **{selected_pay_experience}** because that experience band is already selected in the sidebar.")
        else:
            selected_pay_experience = st.selectbox(
            "Compare sector pay for",
                pay_experience_options,
                key="pay_experience_compare",
            )
    else:
        selected_pay_experience = "All experience bands"

    pay_salary_rows = (
        salary_rows
        if selected_pay_experience == "All experience bands"
        else salary_rows[salary_rows["experience_group"] == selected_pay_experience]
    )
    pay_market_salary_frame = (
        market_salary_frame
        if selected_pay_experience == "All experience bands"
        else market_salary_frame[
            market_salary_frame["experience_group"] == selected_pay_experience
        ]
    )
    pay_scope_salary = (
        pay_salary_rows[SALARY_COL].median() if not pay_salary_rows.empty else float("nan")
    )
    pay_market_median_salary = (
        pay_market_salary_frame[SALARY_COL].median()
        if not pay_market_salary_frame.empty
        else float("nan")
    )

    sector_salary = (
        pay_market_salary_frame.groupby("main_category")[SALARY_COL]
        .agg(median_salary="median", postings="size")
        .reset_index()
    )
    sector_salary = sector_salary[sector_salary["postings"] >= min_n].sort_values(
        "median_salary", ascending=False
    )

    with s1:
        # Left panel = "what should I budget?" benchmark.
        if comparison_mode and sector_filter_active:
            st.subheader(
                f"{scope_label.capitalize()} pay {money_md(pay_scope_salary)} vs a sector median of {money_md(sector_salary['median_salary'].median() if not sector_salary.empty else float('nan'))}"
            )
        elif comparison_mode:
            st.subheader(
                benchmark_title(
                    scope_label,
                    pay_scope_salary,
                    pay_market_median_salary,
                    "pay",
                    money_metric=True,
                )
            )
        elif sector_salary.empty:
            st.subheader("Sector pay benchmark")
        else:
            salary_leader = sector_salary.iloc[0]
            st.subheader(
                f"{salary_leader['main_category']} has the highest median pay in this view"
            )
        if comparison_mode and sector_filter_active:
            pay_benchmark_chart = peer_sector_comparison_chart(
                sector_salary,
                scope_label,
                pay_scope_salary,
                "median_salary",
                "Median monthly salary (S$)",
                highlight_sector=selected_sector_name,
                ascending=False,
            )
            st.altair_chart(pay_benchmark_chart)
            st.caption(
                f"The orange marker is the selected scope. The dashed line is the median sector pay benchmark across the wider market for the same period"
                f"{'' if selected_pay_experience == 'All experience bands' else f' for {selected_pay_experience} roles'}."
            )
        elif comparison_mode:
            pay_benchmark_chart = benchmark_bar_chart(
                scope_label,
                pay_scope_salary,
                pay_market_median_salary,
                "Median monthly salary (S$)",
                height=260,
            )
            st.altair_chart(pay_benchmark_chart)
            st.caption(
                f"The selected scope pays **{money_md(pay_scope_salary)}** versus **{money_md(pay_market_median_salary)}** for the wider market in the same period"
                f"{'' if selected_pay_experience == 'All experience bands' else f' for {selected_pay_experience} roles'}."
            )
        elif sector_salary.empty:
            st.caption("No sector has enough salary-clean rows for a stable benchmark.")
        else:
            top_salary_chart = sector_salary.head(10).copy()
            sal_chart = top_highlight_chart(
                top_salary_chart,
                "main_category",
                "median_salary",
                salary_leader["main_category"],
                "Median monthly salary (S$)",
            )
            st.altair_chart(sal_chart)
            st.caption(
                f"The highlighted bar shows the most expensive sector in this view at **{money_md(salary_leader['median_salary'])}** median monthly pay."
            )

    with s2:
        single_experience_selected = len(filters["experience"]) == 1
        # Right panel = how pay steps up as experience changes.
        if salary_exp.empty:
            st.subheader("Experience-band pay and demand")
        elif single_experience_selected:
            st.subheader(
                f"The selected view is already focused on {filters['experience'][0]} roles"
            )
        else:
            st.subheader(experience_pay_story(salary_exp))
        if salary_exp.empty:
            st.caption("No salary-clean rows are available in this view.")
        elif single_experience_selected:
            st.caption(
                "Because one experience band is already selected in the sidebar, the sector pay comparison and the salary trend below are the more useful apples-to-apples views here."
            )
        else:
            exp_chart_data = salary_exp.dropna().copy()
            exp_chart_data["posting_label"] = exp_chart_data["postings"].apply(
                lambda v: compact_value_label(v, "integer")
            )
            exp_chart_data["salary_label"] = exp_chart_data["median_salary"].apply(
                lambda v: compact_value_label(v, "money")
            )
            bars = (
                alt.layer(
                    alt.Chart(exp_chart_data)
                    .mark_bar(color="#cbd5e1")
                    .encode(
                        x=alt.X("experience_group:N", sort=EXPERIENCE_ORDER, title="Experience band"),
                        y=alt.Y("postings:Q", title="Salary-clean postings"),
                        tooltip=["experience_group", "median_salary", "postings"],
                    ),
                    alt.Chart(exp_chart_data)
                    .mark_text(dy=-8, fontSize=11, color="#475569")
                    .encode(
                        x=alt.X("experience_group:N", sort=EXPERIENCE_ORDER, title="Experience band"),
                        y=alt.Y("postings:Q", title="Salary-clean postings"),
                        text="posting_label:N",
                    ),
                )
            )
            line = (
                alt.layer(
                    alt.Chart(exp_chart_data)
                    .mark_line(point=True, color="#2563eb")
                    .encode(
                        x=alt.X("experience_group:N", sort=EXPERIENCE_ORDER, title="Experience band"),
                        y=alt.Y("median_salary:Q", title="Median monthly salary (S$)"),
                        tooltip=["experience_group", "median_salary", "postings"],
                    ),
                    alt.Chart(exp_chart_data)
                    .mark_text(dy=-10, fontSize=11, color="#1d4ed8")
                    .encode(
                        x=alt.X("experience_group:N", sort=EXPERIENCE_ORDER, title="Experience band"),
                        y=alt.Y("median_salary:Q", title="Median monthly salary (S$)"),
                        text="salary_label:N",
                    ),
                )
            )
            st.altair_chart(
                alt.layer(bars, line).resolve_scale(y="independent").properties(height=360)
            )
            st.caption(
                "Bars show where the hiring volume sits, while the line shows how pay steps up across experience bands."
            )

    selected_experience_band = (
        filters["experience"][0] if len(filters["experience"]) == 1 else None
    )
    if sector_filter_active and selected_experience_band is not None:
        # When the user picks one sector and one experience band, switch to a peer comparison
        # instead of showing every experience band again.
        st.subheader(
            f"{scope_label.capitalize()} {selected_experience_band} pay compared with peer sectors"
        )
        sector_salary_trend = build_sector_salary_trend_data(
            pay_market_salary_frame,
            pay_salary_rows,
            scope_label,
            filters["categories"],
            selected_experience_band,
            top_n=8,
        )
        if sector_salary_trend.empty:
            st.caption("No salary trend is available in this view.")
        else:
            sector_series_order = [scope_label, "Sector median"] + [
                name
                for name in sector_salary_trend["series_label"].unique().tolist()
                if name not in [scope_label, "Sector median"]
            ]
            sector_color_map = {
                scope_label: "#c2410c",
                "Sector median": "#0f172a",
            }
            peer_palette = [
                "#0f766e",
                "#1d4ed8",
                "#7c3aed",
                "#64748b",
                "#0284c7",
                "#059669",
                "#9333ea",
                "#475569",
            ]
            for name, color in zip(
                [n for n in sector_series_order if n not in sector_color_map],
                peer_palette,
            ):
                sector_color_map[name] = color
            sector_salary_chart = (
                alt.Chart(sector_salary_trend)
                .mark_line(point=True)
                .encode(
                    x=alt.X("posting_month:T", title="Posting month"),
                    y=alt.Y("median_salary:Q", title="Median monthly salary (S$)"),
                    detail="series_label:N",
                    color=alt.Color(
                        "series_label:N",
                        scale=alt.Scale(
                            domain=sector_series_order,
                            range=[sector_color_map[name] for name in sector_series_order],
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    size=alt.Size("line_size:Q", legend=None),
                    opacity=alt.Opacity("line_opacity:Q", legend=None),
                    tooltip=["posting_month:T", "series_label:N", "median_salary:Q"],
                )
                .properties(height=380)
            )
            st.altair_chart(sector_salary_chart)
            st.caption(
                f"This compares **{selected_experience_band}** pay in the selected scope against the median for that same experience band and the largest peer sectors."
            )
    elif salary_trend.empty:
        st.subheader(salary_trend_heading)
        st.caption("No salary trend is available in this view.")
    else:
        st.subheader(salary_trend_heading)
        trend_salary_chart = (
            alt.Chart(salary_trend.dropna())
            .mark_line(point=True)
            .encode(
                x=alt.X("posting_month:T", title="Posting month"),
                y=alt.Y("median_salary:Q", title="Median monthly salary (S$)"),
                color=alt.Color("experience_group:N", sort=EXPERIENCE_ORDER, title="Experience band"),
                tooltip=["posting_month:T", "experience_group", "median_salary"],
            )
            .properties(height=380)
        )
        st.altair_chart(trend_salary_chart)
        st.caption(salary_trend_caption)

# Tab 3: hiring difficulty and applicant response
with tab3:
    t1, t2 = st.columns(2)

    with t1:
        # This panel focuses on applicant supply: how many applicants each vacancy seems to attract.
        selected_sector_row = None

        if market_bench.empty:
            st.subheader("Sector hiring map")
            st.caption("Not enough stable sector data is available for a hiring benchmark.")
        elif comparison_mode and sector_filter_active:
            st.subheader(
                f"{scope_label.capitalize()} receive {mean_apv:.2f} applications per vacancy vs a sector median of {sector_median_apv:.2f}"
            )
            apv_benchmark_chart = peer_sector_comparison_chart(
                market_bench,
                scope_label,
                mean_apv,
                "mean_applications_per_vacancy",
                "Applications per vacancy",
                highlight_sector=selected_sector_name,
                ascending=False,
            )
            st.altair_chart(apv_benchmark_chart)
            st.caption(
                "The orange marker is the selected scope. The dashed line is the median sector benchmark, and the grey markers are the eight largest peer sectors in the same period."
            )
        else:
            selected_sector_name = st.selectbox(
                "Highlight a sector on the map",
                market_bench["main_category"].tolist(),
                index=0,
            )
            selected_sector_row = market_bench[
                market_bench["main_category"] == selected_sector_name
            ].iloc[0]
            st.subheader(selected_sector_title(selected_sector_row, market_bench))

            x_rule = pd.DataFrame({"median_salary": [market_bench["median_salary"].median()]})
            y_rule = pd.DataFrame(
                {
                    "mean_applications_per_vacancy": [
                        market_bench["mean_applications_per_vacancy"].median()
                    ]
                }
            )

            chart_data = market_bench.copy()
            chart_data["highlight_group"] = chart_data["main_category"].apply(
                lambda x: "Highlighted sector" if x == selected_sector_name else "Other sectors"
            )

            points = (
                alt.Chart(chart_data)
                .mark_circle(size=180, opacity=0.85)
                .encode(
                    x=alt.X("median_salary:Q", title="Median monthly salary (S$)"),
                    y=alt.Y(
                        "mean_applications_per_vacancy:Q",
                        title="Average applications per vacancy",
                    ),
                    color=alt.Color(
                        "highlight_group:N",
                        scale=alt.Scale(
                            domain=["Highlighted sector", "Other sectors"],
                            range=["#c2410c", "#cbd5e1"],
                        ),
                        legend=None,
                    ),
                    size=alt.Size("postings:Q", title="Postings"),
                    tooltip=[
                        "main_category",
                        "postings",
                        "median_salary",
                        "mean_applications_per_vacancy",
                        "mean_application_rate",
                        "tightness_label",
                    ],
                )
            )
            point_labels = (
                alt.Chart(chart_data[chart_data["highlight_group"] == "Highlighted sector"])
                .mark_text(dx=10, dy=-10, fontSize=11, color="#9a3412")
                .encode(
                    x=alt.X("median_salary:Q", title="Median monthly salary (S$)"),
                    y=alt.Y(
                        "mean_applications_per_vacancy:Q",
                        title="Average applications per vacancy",
                    ),
                    text=alt.Text("main_category:N"),
                )
            )

            vline = alt.Chart(x_rule).mark_rule(strokeDash=[6, 4], color="gray").encode(
                x="median_salary:Q"
            )
            hline = alt.Chart(y_rule).mark_rule(strokeDash=[6, 4], color="gray").encode(
                y="mean_applications_per_vacancy:Q"
            )

            st.altair_chart((points + point_labels + vline + hline).properties(height=380))
            st.caption(
                f"{selected_sector_name} offers median pay of **{money_md(selected_sector_row['median_salary'])}** "
                f"and attracts **{selected_sector_row['mean_applications_per_vacancy']:.2f} applications per vacancy**. "
                "Use the selector and hover to inspect each sector. Sectors that sit lower and further right are usually tougher for employers."
            )

    with t2:
        # This panel compresses several hiring signals into one tighter/looser ranking.
        if market_bench.empty:
            st.subheader("Sector tightness ranking")
        elif comparison_mode and sector_filter_active:
            st.subheader(
                f"{scope_label.capitalize()} rank {ordinal_text(selected_tightness_rank)} tightest out of {len(market_bench)} sectors"
            )
        else:
            tightest_sector = market_bench.iloc[0]
            st.subheader(
                f"{tightest_sector['main_category']} ranks as the hardest sector to hire for in this view"
            )
        if market_bench.empty:
            st.caption("No stable tightness benchmark is available.")
        elif comparison_mode and sector_filter_active:
            rate_benchmark_chart = tightness_rank_chart(
                market_bench,
                highlight_sector=selected_sector_name,
                selected_score=selected_tightness_score,
                selected_label=scope_label,
                top_n=8,
                height=320,
            )
            st.altair_chart(rate_benchmark_chart)
            st.caption(
                f"Hiring tightness is a weighted score built from **lower applications per vacancy (45%)**, **lower application rate (35%)**, and **higher pay (20%)**. "
                f"Higher scores mean the sector appears harder for employers to hire into. The chart includes both the tightest and loosest sectors so **{selected_sector_name}** can be judged against both ends of the market. The selected scope scores **{selected_tightness_score:.2f}**."
                if selected_tightness_score is not None
                else "Higher scores mean the sector appears harder for employers to hire into."
            )
        else:
            st.altair_chart(tightness_rank_chart(market_bench))
            st.caption(
                f"The current tightest sector is **{tightest_sector['main_category']}** with "
                f"median pay of **{money_md(tightest_sector['median_salary'])}**, "
                f"**{tightest_sector['mean_applications_per_vacancy']:.2f} applications per vacancy**, "
                f"and an average application rate of **{tightest_sector['mean_application_rate']:.1%}**. "
                "Higher scores mean harder hiring conditions. The chart shows both the tightest and loosest sectors so the spread is visible."
            )

    if single_sector_mode:
        heatmap_data, heatmap_group_order = role_experience_heatmap_data(
            filtered_df,
            selected_sector_name,
            top_n=8,
            min_postings=25,
        )
        heatmap_group_col = "primary_role"
        heatmap_group_title = "Role family"
    else:
        heatmap_data, heatmap_group_order = sector_experience_heatmap_data(
            market_view_df,
            selected_categories=filters["categories"],
            top_n=8,
            min_postings=25,
        )
        heatmap_group_col = "main_category"
        heatmap_group_title = "Sector"

    if heatmap_data.empty:
        st.subheader("Where hiring looks thinnest across sectors and experience bands")
        st.caption("Not enough stable data is available for a heatmap view.")
    else:
        st.subheader(
            heatmap_story_title(
                heatmap_data,
                single_sector_mode,
                selected_sector_name,
                heatmap_group_col,
            )
        )
        st.altair_chart(
            sector_experience_heatmap_chart(
                heatmap_data,
                heatmap_group_order,
                "",
                group_col=heatmap_group_col,
                group_title=heatmap_group_title,
            )
        )
        st.caption(
            (
                "Each cell shows **applications per vacancy** for one role-family-and-experience combination. "
                if single_sector_mode
                else "Each cell shows **applications per vacancy** for one sector-and-experience combination. "
            )
            + "Darker red cells mean thinner applicant depth and usually harder hiring conditions, while blue cells mean deeper applicant pools. "
            + (
                "Because one sector is already selected, the rows switch to the main role families inside that sector so you can see which combinations are hardest to hire for."
                if single_sector_mode
                else (
                "The selected sector appears first, followed by the largest peer sectors in the same date window."
                if sector_filter_active
                else "This heatmap shows the largest sectors in the selected date window."
                )
            )
        )

    if single_sector_mode and not seniority_bench.empty:
        # For a single sector, comparing experience bands is more useful than comparing sectors again.
        toughest_band = seniority_bench.sort_values(
            "mean_applications_per_vacancy", ascending=True
        ).iloc[0]
        strongest_band = seniority_bench.sort_values(
            "mean_applications_per_vacancy", ascending=False
        ).iloc[0]
        st.subheader(
            f"Inside {selected_sector_name}, {toughest_band['experience_group']} roles draw the fewest applicants"
        )
    elif role_bench.empty:
        st.subheader("Role-family response benchmark")
    elif comparison_mode and len(role_bench) <= 1:
        st.subheader("Job-family response sits close to the selected scope average")
    else:
        weakest_role = role_bench.iloc[0]
        st.subheader(
            f"{weakest_role['primary_role']} draws only {weakest_role['mean_applications_per_vacancy']:.2f} applications per vacancy"
        )
    if single_sector_mode and not seniority_bench.empty:
        seniority_chart_data = seniority_bench.dropna().copy()
        seniority_chart_data["highlight_group"] = seniority_chart_data[
            "experience_group"
        ].apply(
            lambda x: "Fewest applicants"
            if x == toughest_band["experience_group"]
            else (
                "Most applicants"
                if x == strongest_band["experience_group"]
                else "Other bands"
            )
        )
        seniority_chart = (
            alt.layer(
                alt.Chart(seniority_chart_data)
                .mark_bar(cornerRadiusEnd=5)
                .encode(
                    x=alt.X(
                        "mean_applications_per_vacancy:Q",
                        title="Average applications per vacancy",
                    ),
                    y=alt.Y(
                        "experience_group:N",
                        sort=EXPERIENCE_ORDER,
                        title=None,
                    ),
                    color=alt.Color(
                        "highlight_group:N",
                        scale=alt.Scale(
                            domain=["Fewest applicants", "Most applicants", "Other bands"],
                            range=["#b91c1c", "#2563eb", "#cbd5e1"],
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=[
                        "experience_group",
                        "postings",
                        alt.Tooltip("mean_applications_per_vacancy:Q", format=".2f"),
                        alt.Tooltip("mean_application_rate:Q", format=".1%"),
                    ],
                ),
                alt.Chart(seniority_chart_data)
                .mark_text(align="left", dx=6, fontSize=11, color="#334155")
                .encode(
                    x=alt.X(
                        "mean_applications_per_vacancy:Q",
                        title="Average applications per vacancy",
                    ),
                    y=alt.Y(
                        "experience_group:N",
                        sort=EXPERIENCE_ORDER,
                        title=None,
                    ),
                    text=alt.Text("mean_applications_per_vacancy:Q", format=".2f"),
                ),
            )
            .properties(height=300)
        )
        st.altair_chart(seniority_chart)
        st.caption(
            f"Within **{selected_sector_name}**, **{strongest_band['experience_group']}** roles attract **{strongest_band['mean_applications_per_vacancy']:.2f} applications per vacancy**, "
            f"versus only **{toughest_band['mean_applications_per_vacancy']:.2f}** for **{toughest_band['experience_group']}** roles. "
            "The red bar marks the toughest band to hire for, while the blue bar marks the band with the strongest applicant response."
        )
        single_sector_role_bench = role_panel_benchmarks(role_bench, selected_sector_name)
        role_panel_label = (
            "core tech role-family response"
            if selected_sector_name == "Information Technology"
            else "role-family response"
        )
        if single_sector_role_bench.empty:
            st.subheader(f"Inside {selected_sector_name}, role-family response is not available")
            st.caption(
                f"No classified {role_panel_label} benchmark is available for this sector view."
            )
        elif len(single_sector_role_bench) <= 1:
            st.subheader(f"Inside {selected_sector_name}, role-family response is limited")
            st.caption(
                f"Only one classified {role_panel_label} group meets the minimum posting threshold in this sector view, so there is not enough variation for a meaningful internal comparison."
            )
        else:
            weakest_role = single_sector_role_bench.iloc[0]
            weakest_roles = single_sector_role_bench.head(8).copy()
            weakest_roles["highlight_group"] = weakest_roles["primary_role"].apply(
                lambda x: "Weakest response"
                if x == weakest_role["primary_role"]
                else "Other roles"
            )
            role_order = weakest_roles["primary_role"].tolist()
            st.subheader(
                f"Inside {selected_sector_name}, {weakest_role['primary_role']} draws the fewest applicants among core tech roles"
                if selected_sector_name == "Information Technology"
                else f"Inside {selected_sector_name}, {weakest_role['primary_role']} roles draw the fewest applicants"
            )
            weak_role_chart = (
                alt.layer(
                    alt.Chart(weakest_roles)
                    .mark_bar(cornerRadiusEnd=5)
                    .encode(
                        x=alt.X(
                            "mean_applications_per_vacancy:Q",
                            title="Average applications per vacancy",
                        ),
                        y=alt.Y(
                            "primary_role:N",
                            sort=role_order,
                            title=None,
                        ),
                        color=alt.Color(
                            "highlight_group:N",
                            scale=alt.Scale(
                                domain=["Weakest response", "Other roles"],
                                range=["#b91c1c", "#cbd5e1"],
                            ),
                            legend=None,
                        ),
                        tooltip=[
                            "primary_role",
                            "postings",
                            alt.Tooltip(
                                "mean_applications_per_vacancy:Q",
                                format=".2f",
                            ),
                            alt.Tooltip("mean_application_rate:Q", format=".1%"),
                        ],
                    ),
                    alt.Chart(weakest_roles)
                    .mark_text(align="left", dx=6, fontSize=11, color="#334155")
                    .encode(
                        x=alt.X(
                            "mean_applications_per_vacancy:Q",
                            title="Average applications per vacancy",
                        ),
                        y=alt.Y(
                            "primary_role:N",
                            sort=role_order,
                            title=None,
                        ),
                        text=alt.Text("mean_applications_per_vacancy:Q", format=".2f"),
                    ),
                )
                .properties(height=320)
            )
            st.altair_chart(weak_role_chart)
            st.caption(
                f"Within **{selected_sector_name}**, **{weakest_role['primary_role']}** attracts only **{weakest_role['mean_applications_per_vacancy']:.2f} applications per vacancy**. "
                + (
                    "This compares only the main core tech role families inside IT, so the benchmark stays focused on hiring needs a technology employer would usually care about."
                    if selected_sector_name == "Information Technology"
                    else "This compares the main classified role families inside the selected sector and excludes `Other / Unclassified` so the benchmark stays readable."
                )
            )
    elif role_bench.empty:
        st.caption("No job-family benchmark is available for this view.")
    elif comparison_mode and len(role_bench) <= 1:
        st.caption(
            "The selected scope is already narrow enough that an internal job-family comparison is not very informative, so the whole-market benchmarks above are more useful."
        )
    else:
        weakest_roles = role_bench.head(10).copy()
        weakest_roles["highlight_group"] = weakest_roles["primary_role"].apply(
            lambda x: "Weakest response" if x == weakest_role["primary_role"] else "Other roles"
        )
        weak_role_chart = (
            alt.layer(
                alt.Chart(weakest_roles)
                .mark_circle(size=180)
                .encode(
                    x=alt.X(
                        "mean_applications_per_vacancy:Q",
                        title="Average applications per vacancy",
                    ),
                    y=alt.Y("primary_role:N", sort="x", title=None),
                    color=alt.Color(
                        "highlight_group:N",
                        scale=alt.Scale(
                            domain=["Weakest response", "Other roles"],
                            range=["#b91c1c", "#cbd5e1"],
                        ),
                        legend=None,
                    ),
                    tooltip=[
                        "primary_role",
                        "postings",
                        "mean_applications_per_vacancy",
                        "mean_application_rate",
                    ],
                ),
                alt.Chart(weakest_roles)
                .mark_text(dx=10, fontSize=11, color="#334155")
                .encode(
                    x=alt.X(
                        "mean_applications_per_vacancy:Q",
                        title="Average applications per vacancy",
                    ),
                    y=alt.Y("primary_role:N", sort="x", title=None),
                    text=alt.Text("mean_applications_per_vacancy:Q", format=".2f"),
                ),
            )
            .properties(height=340)
        )
        st.altair_chart(weak_role_chart)
        st.caption(
            f"**{weakest_role['primary_role']}** attracts only **{weakest_role['mean_applications_per_vacancy']:.2f} applications per vacancy**, "
            f"which is well below the filtered-view average of **{mean_apv:.2f}**. "
            "This chart excludes `Other / Unclassified` so the comparison stays usable."
        )

# Tab 4: raw rows behind the current view
with tab4:
    # Keep the data preview visible so teammates can audit the chart logic against real rows.
    st.subheader("Filtered data preview")
    show_cols = [
        "title",
        "postedCompany_name",
        "main_category",
        "primary_role",
        "positionLevels",
        "employmentTypes",
        "minimumYearsExperience",
        "experience_group",
        "salary_min_clean",
        "salary_max_clean",
        SALARY_COL,
        "metadata_totalNumberJobApplication",
        "numberOfVacancies",
        "applications_per_vacancy",
        "metadata_totalNumberOfView",
        "application_rate",
        "metadata_originalPostingDate",
    ]
    show_cols = [col for col in show_cols if col in filtered_df.columns]

    st.dataframe(
        filtered_df[show_cols].head(1000),
        hide_index=True,
        column_config={
            "salary_min_clean": st.column_config.NumberColumn("Salary min", format="S$ %.0f"),
            "salary_max_clean": st.column_config.NumberColumn("Salary max", format="S$ %.0f"),
            SALARY_COL: st.column_config.NumberColumn("Average salary", format="S$ %.0f"),
            "applications_per_vacancy": st.column_config.NumberColumn(
                "Apps / vacancy", format="%.2f"
            ),
            "application_rate": st.column_config.NumberColumn(
                "Application rate", format="percent"
            ),
            "metadata_originalPostingDate": st.column_config.DateColumn(
                "Posting date", format="YYYY-MM-DD"
            ),
        },
    )
    st.caption("Showing the first 1,000 filtered rows.")

    st.download_button(
        "Download filtered rows as CSV",
        filtered_df[show_cols].to_csv(index=False).encode("utf-8"),
        file_name="filtered_jobs.csv",
        mime="text/csv",
    )
