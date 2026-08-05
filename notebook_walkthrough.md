# EDA_Clean_Merged — Plain-English Walkthrough

A section-by-section guide to what the notebook does and why, followed by a
breakdown of the Streamlit dashboard.

---

## Part A — The Notebook

### The one idea that shapes everything

A job posting can belong to **several industries at once**. Roughly a third of
yours do (33.6%).

That forces two tables:

| Table | Grain | Used for |
|---|---|---|
| `df_clean` | one row per **posting** | headline counts, salary medians, company counts |
| `df_categories` | one row per **posting × industry** | industry breakdowns only |

If you counted postings off the exploded table, a job in 3 industries would
count 3 times. Every design choice downstream follows from keeping these
separate.

---

### 1. Exploratory Data Analysis

**Does:** Loads `SGJobData.csv`, runs `.info()` and `.describe()`.

**Finds:** 1,048,585 rows × 22 columns. `occupationId` is 100% empty. 3,988
rows are blank across most columns. Dates are stored as text. `categories`
looks like JSON but is a string. Salaries have wild outliers.

This is the diagnosis step — everything after it is treatment.

---

### 1.1 Removing Missing Values

**Does:** Drops the `occupationId` column entirely, then drops the 3,988 rows
that have no `metadata_jobPostId`.

**Why this order matters:** A plain `df.dropna()` would have deleted *every
row*, because `occupationId` is null everywhere. So the empty column goes
first (by column), then the empty rows go (by row), using job ID as the test
for "is there anything here at all".

---

### 1.2 Parsing Dates

**Does:** Converts three date columns from text to real datetimes with
`errors='coerce'`.

**Result:** Zero unparseable dates. Data spans **2022-10-03 → 2024-05-29**.

`coerce` means a bad date becomes `NaT` instead of crashing the run — then the
next cell counts them so nothing fails silently.

---

### 1.3 Cleaning `title`

**Does:** Runs five regex rules in sequence over a lowercased copy of `title`
to produce `title_clean`.

| Rule | Removes |
|---|---|
| `recruiter_code` | leading codes like `1234 - ` |
| `hashtags` | `#urgenthiring` |
| `other_numbers` | alphanumeric tokens containing digits |
| `punction_emojis` | anything not a-z |
| `collapse_ws` | runs of spaces |

**Safety net:** if cleaning strips a title to nothing, it falls back to the
original lowercased title rather than leaving a blank.

The original `title` column is kept. `title_clean` is a new column — nothing
is destroyed.

---

### 1.4 Removing Duplicate Postings

**Does:** Sorts by original posting date, then drops duplicates on four keys —
`title_clean`, `postedCompany_name`, `salary_minimum`, `salary_maximum` —
keeping the **first**.

**Result:** 1,044,597 → **629,246 rows** (about 40% removed).

**The logic:** when a posting expires, employers repost the identical job.
Same title + same company + same salary range = the same vacancy. Sorting by
date first means you keep the *original* advertisement, not a repost, which
keeps the time series honest.

---

### 1.5 Position Level vs Experience

**Does:** Flags three contradictions —

- Senior label with under 3 years required: **16,408**
- Junior label with over 20 years required: **28**
- Any experience over 40 years: **19**

**The key move:** it does **not** delete these rows. It blanks the untrusted
*value* and keeps the row:

```python
df_clean['positionLevels_clean'] = df_clean['positionLevels'].where(~contradictory)
```

`positionLevels_clean` is null for 2.6% of postings. The comment says it
plainly: *"If we drop rows, it will affect hiring pool depth."* A job with a
mislabelled seniority is still a real vacancy.

This "blank the value, keep the row" pattern is the notebook's house style,
and it recurs in 1.6.1.

---

### 1.6 Salary Bounds and Raw Average

**Does three things:**

1. Turns `0` salaries into `NaN` (zero means "not stated", not "unpaid")
2. Computes `average_salary` = midpoint of min and max
3. Builds `df_clean_salary` — a **copy** filtered to $500–$60,000

**Important:** step 3 creates a *separate frame*. `df_clean` still has every
row, outliers included. Nothing is deleted here.

Steps 1 and 2 are load-bearing for everything downstream. Step 3 is only used
later as a before/after comparison number.

> The section title says "Dealing with Salary Outliers", which reads like
> removal. It isn't — removal happens in Analysis Grouping. Worth retitling to
> *"Salary bounds and raw average"*.

---

### 1.6.1 Converting Hourly and Annual to Monthly

**Does:** Repairs salaries quoted in the wrong unit. Four rules, applied in
strict order.

| Step | Rule | Count |
|---|---|---|
| Setup | max < 5 → blank both ends | — |
| Setup | min < 1000 **and** min < 0.2 × max → mirror max into min | 1,331 |
| Hourly | min ≤ 100 → × hours × 52 ÷ 12 (44.3h full-time, 21h part-time) | 3,465 |
| Annual max | wide range, max > 20,500, max÷12 ≥ min → max ÷ 12 | 159 |
| Too wide | wide range that failed the test → collapse both ends to average | 288 |
| Both annual | min > 24,000 → both ÷ 12 | 602 |

**Two deliberate refusals to convert:**

- 40 senior-management roles quoting hourly-looking rates → flagged as
  `salary_level_mismatch`, not multiplied up. A Senior Manager at "$4" is a
  typo, not an hourly job.
- 128 junior roles with max ≥ $20,000 → also flagged.

**Order is load-bearing.** `both_annual` runs *last* on purpose, because
earlier fixes can reveal a minimum as annual. A row that looks broken after
step 2 may be fine after step 4 — which is exactly why nothing is filtered
inside this section.

**Three safety checks:**

- `assert` that the annual rules don't overlap
- `assert` that no range ends up inverted (max below min)
- a printed count of postings **rescued** into the usable band: **3,815**

**Outliers still remain afterwards** (`average_salary_clean` ranges 4 →
1,056,816). That's expected — this section repairs, it doesn't judge. Junk
too broken to repair survives to be flagged in Analysis Grouping.

---

### 1.7 Parsing Categories

**Does:** Turns the JSON string `[{"id":21,"category":"Information Technology"}]`
into two parallel Python list columns — `category_id_list` and `category_list`
— plus `n_categories` and `main_category`.

**Written defensively:** `parse_categories()` returns `[]` on any failure
(null, bad JSON, wrong type, missing key) rather than raising. One malformed
row can't kill a run over 629k rows. It also de-duplicates within a posting
while preserving order.

**Result:** 0 parse failures. 33.6% of postings sit in more than one industry.
1,014,871 posting × category pairs total.

The two lists are built from the same parsed tuples, so they're guaranteed the
same length per row — which is what lets them explode together in 1.8.

`main_category` (first category only) exists for quick groupbys, with a
comment warning that user-facing filters must use the full list.

---

### 1.8 Exploding into the Long Table

**Does:** `.explode()` on both list columns at once, producing
`df_categories` at **1,014,871 rows** — one per posting × industry.

Exploding two columns in a single call keeps IDs aligned with names. Doing it
in two separate calls would scramble the pairing.

### 1.8.1 Category Lookup

Builds a reference table of **43 distinct industries** with a posting count
each. Sanity check and documentation in one.

---

### 1.9 Job Roles from Titles

**Does:** Runs 19 regex patterns over `title_clean` to tag each posting with
every role family it matches, producing `role_list` and `primary_role`.

Covers the "by role" half of the brief, alongside the "by industry" half from
categories. Titles matching nothing get `'Other / Unclassified'` — visible
rather than silently dropped.

Same one-to-many shape as categories: a title can match several role families.

---

### Analysis Grouping

**This is where filtering finally happens.**

```python
salary_reliable = average_salary_clean.between(500, 60000)
                  & ~salary_level_mismatch
```

**Result:** 626,996 usable rows (**99.6%**), a **net gain of +3,687** over the
raw filter from 1.6.

Then an `assert` proves no posting that was usable *before* conversion became
unusable *after* — i.e. the conversion could only help, never hurt. That
assertion passing is the notebook's strongest quality claim.

Also builds:

- `experience_group` — 0-1 / 2-4 / 5-9 / 10+ years
- `employment_group` — Standard / Flexible / Internship
- `posting_month` — for the time series

---

### Writing Files

Joins the three list columns with `|` (CSV can't store Python lists), drops
the now-redundant raw `categories` JSON, and writes:

- `SGJobData_clean.csv` — one row per posting
- `SGJobData_categories.csv` — one row per posting × industry

---

## Part B — The Streamlit Dashboard

Reading the file top to bottom, in ten numbered sections.

---

### The header comment (lines 1–21)

Documents the counting rule the whole app depends on:

> Category filters explode to **MATCH**, then join the surviving job IDs back
> to the posting-level frame to **COUNT**.

Match on the long table, count on the wide table. Without the join-back, a
posting in 3 industries counts 3 times in every headline number.

---

### 1. Page config (lines 23–56)

Sets the page to wide layout with the sidebar open.

`_W` is a version-shim: Streamlit renamed `use_container_width` to `width` in
1.49, so this picks the right keyword at runtime and gets spread into every
chart call as `**_W`. Purely defensive — it stops the app breaking on a
different Streamlit version.

---

### 2. Loading data (lines 59–94)

```python
split_list_col()   # turns "A|B|C" back into ["A","B","C"]
load_data()        # reads the clean CSV, rebuilds list columns
load_categories()  # reads the exploded CSV
option_lists()     # builds the dropdown choices
```

**`@st.cache_data` is the important part.** Streamlit re-runs the entire
script top-to-bottom on *every* interaction — every checkbox, every dropdown.
Without caching you'd re-read a 600k-row CSV each time. With it, the read
happens once and the result is reused.

The dropdown options come from the data itself, so no filter can offer a
choice that doesn't exist.

Line 70 has a fallback: if `average_salary_clean` is missing, use
`average_salary`. Safety net for a stale CSV.

---

### 3. Sidebar filters (lines 96–156)

Every control the user gets:

| Control | What it does |
|---|---|
| Reset all filters | Clears session state, reruns |
| Date range | Uses `originalPostingDate`, not `newPostingDate`, so a repost doesn't shift a job into a later month |
| Industry + Any/All | Multiselect; "Any" = at least one match, "All" = must carry every one selected |
| Role family | Multiselect from `role_list` |
| Title contains | Free-text search, comma-separated for OR |
| Position / Employment / Experience | Three multiselects |
| Usable salary rows only | The `salary_reliable` toggle — **on by default** |
| Min postings for a benchmark | Default 30; hides thin slices from median charts |

That last one is quietly the most important for credibility. A median over 4
postings is noise presented as a benchmark. This makes the threshold explicit
and user-adjustable.

The Any/All radio is `disabled` until an industry is picked — the control
can't be used in a meaningless state.

---

### 4. Applying filters (lines 159–225)

**`ids_matching_list_col()`** — the heart of the app.

Because `category_list` holds a *list* per row, you can't just use `.isin()`.
So it:

1. Explodes to one row per posting × category
2. Keeps rows matching what the user selected
3. **"Any" mode:** returns those job IDs
4. **"All" mode:** counts distinct matches per job, keeps only jobs whose
   count equals the number selected
5. Returns **IDs only** — never the exploded rows

**`apply_filters()`** then uses `.isin(those_ids)` against the wide table. The
result, `filtered_df`, stays at one row per posting no matter how many
industries a job belongs to. That's the join-back the header comment
describes.

Two details worth noting:

- Line 187–188 escapes regex metacharacters in the title search, so a user
  typing `C++` doesn't crash the app
- Lines 201–206 handle a Streamlit quirk: `date_input` briefly returns a
  *single* date mid-selection, so the code checks it got a proper pair before
  unpacking

**`exploded_view()`** (line 214) builds the long table on demand for charts
that are *supposed* to count per industry. Its docstring says explicitly that
this is wrong for headline counts. Note it carries `salary_reliable` through
the explode — without that, line 338 would fail.

---

### 5. Header (lines 228–244)

Title, one-line explanation, and an early exit: if the filters match nothing,
show a warning and `st.stop()` rather than letting every chart below throw its
own error.

The caption shows how many postings survived filtering and the actual date
span — so the user always knows how much data is behind what they're looking
at.

---

### 6. Metric cards (lines 247–285)

Four numbers across the top.

```python
salary_rows = filtered_df[filtered_df["salary_reliable"]]   # this selection
market      = df[df["salary_reliable"]]                     # whole market
```

Note both lines are **unconditional** — no `if`. Even if the user turns off
the sidebar checkbox, salary maths still runs only on reliable rows.

| Card | Delta shown |
|---|---|
| Median Salary | vs whole-market median |
| Mean Salary | vs the median above |
| Hiring Companies | none |
| Avg Monthly Vacancies | none |

The mean-vs-median delta is a nice touch: when the mean sits above the median,
a tail of high-paying jobs is pulling it up. The help text says so.

Lines 281–285 tell the user how many postings in their selection had no usable
salary — the exclusion is visible, not silent.

---

### 7. Middle charts (lines 290–329)

**Left — Vacancies over time.** Groups `filtered_df` by `posting_month`, sums
vacancies, plots a Plotly line. Shows hiring pool depth over time. Uses all
filtered postings, since a posting without a salary is still a real vacancy.

**Right — Median salary per position level.** Groups `salary_rows`, computes
median *and* count, drops any level below `min_n`, plots a horizontal bar.

The comment explains a deliberate revision: this used to be a pie chart. Pie
slices imply parts of a whole, and medians don't sum to a whole. A bar chart
compares levels correctly. Good judgement, worth keeping in the writeup.

---

### 8. Bottom row (lines 334–403)

```python
long_all    = exploded_view(filtered_df)          # posting x industry
long_salary = long_all[long_all["salary_reliable"]]
```

**Left (wider) — Median salary, top 10 industries.** Groups `long_salary` by
category, applies `min_n`, takes top 10. The caption states outright that a
multi-industry posting is counted in each — the double-count is intentional
here and declared.

**Right — Position × Employment heatmap.** An Altair `mark_rect` where colour
is the **median** of each cell. The comment explains why median and not max:
one outlier posting shouldn't light up a whole cell. Tooltips show both the
median and the posting count, so a cell built on 3 postings is identifiable.

---

### 9. Distribution and hierarchy (lines 408–441)

**Left — Salary spread by industry.** A scatter where each point is a posting.
Sampled to 5,000 with a fixed `random_state=0` — responsive to render, and
reproducible. Shows the *spread* behind each median, which a bar chart hides.

**Right — Sunburst.** Industry → position level → employment type, as nested
rings. The caption warns that ring sizes count posting × industry pairs, not
unique postings.

Note this one uses `long_all`, not `long_salary` — it's about structure of the
job market, not pay, so salary reliability is irrelevant.

---

### 10. Raw data (lines 446–459)

A collapsed expander with the first 1,000 filtered rows and a download button
for the full filtered set as CSV. Lets a user verify any number in the
dashboard against the underlying postings.

Line 452 filters `show_cols` to columns that actually exist, so a missing
column degrades gracefully instead of raising `KeyError`.

---

## The pattern worth naming in your writeup

The notebook never deletes a row for having one bad field. It **blanks the
value or sets a flag** and keeps the row:

| Section | Bad thing | Response |
|---|---|---|
| 1.5 | Level contradicts experience | Blank `positionLevels_clean` |
| 1.6.1 | Senior role at hourly rate | Flag `salary_level_mismatch` |
| 1.6.1 | Salary too broken to repair | Leave it; flag later |
| Analysis Grouping | Anything unusable | `salary_reliable = False` |

The payoff: a posting with a typo'd salary still counts toward hiring pool
depth and industry demand, where it's perfectly valid data. Only the salary
maths skips it.

And because filtering happens *last*, the unit conversions in 1.6.1 got a
chance to run on rows a naive early filter would have binned — which is
where the 3,815 rescued postings came from.
