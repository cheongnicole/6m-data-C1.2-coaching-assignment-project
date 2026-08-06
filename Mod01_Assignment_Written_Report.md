# Singapore Job Market Benchmarking Dashboard
### Project Report

**Team Members:**
- Nicole, Prakash, Shaun, Tim, Bang Lin

**Data source:** 
- Singapore job postings dataset (`SGJobData.csv`), Oct 2022 – May 2024

**Deliverables:** 
- Main codebase (Jupyter Notebook)
- Cleaned datasets (`SGJobData_clean.csv`, `SGJobData_categories.csv`) 
- A Streamlit dashboard

---

## 1. Business Case & Objective

### Scenario

Companies entering the Singapore market have no reliable, industry-specific benchmark for what talent actually costs, or for how deep the local hiring pool is. Headcount is typically the largest line item in an entry budget, so this gap sits directly under the biggest number in the plan.

Salary data that does exist is either aggregated too coarsely to be actionable, sits behind paid consultancy, or comes from anecdote. Live job postings online is messy and on various platforms, most teams do not attempt to collate and use it.

### Target Users

- **Market-entry and expansion teams** sizing a headcount budget for a new Singapore office
- **Talent acquisition leads** setting offer salary bands that are competitive but not overpaid
- **Consultants and analysts** advising clients on where hiring is feasible and where it is contested

### Objective

Turn 1M+ raw job postings into a decision-ready tool where a client selects a **target industry** or **job role** and receives an evidence-based view of:

- Prevailing salary ranges (median and spread, not just an average)
- Hiring pool depth: how many vacancies exist
- Market structure: which seniority levels and employment types actually get hired for

### Success criteria

| Criterion | How it is met |
|---|---|
| **Trustworthy numbers** | Every headline figure counts each posting exactly once, even though a posting can belong to several industries |
| **Transparent exclusions** | The dashboard states how many postings were excluded from salary maths, rather than dropping them silently |
| **No false precision** | A minimum-posting threshold hides thin slices so a median over a handful of jobs is never presented as a benchmark |
| **Self-service** | A non-technical user can filter to their situation and read an answer without asking an analyst |


---

## 2. Process & Data Handling

### Starting point

1,048,585 rows across 22 columns. Initial inspection showed a column that was entirely empty, ~4,000 rows with no usable content, dates stored as text, an industry field stored as a JSON string rather than parsed data, free-text titles full of recruiter noise, and salary figures spanning several orders of magnitude.

### The design decision

**A posting can belong to more than one industry.** That makes the data one-to-many, which forces two tables rather than one:

| Table | Grain | Used for |
|---|---|---|
| `df_clean` | one row per **posting** | headline counts, salary medians, company counts |
| `df_categories` | one row per **posting × industry** | industry breakdowns only |

Counting postings off the exploded table would count a job in three industries three times. Keeping the two grains separate and being explicit about which one each chart uses streamlines the project. It also helps us address cleaning aspect for the JSON strings.

### Cleaning approach

**Missing data.** The empty column `occupationId` was dropped first, then the empty rows that contained NaN missing values. Running a naive .dropna() on the raw frame would have deleted every row, because one column was null everywhere.

**Dates.** Three dated columns were converted from text to real datetimes, with failures counted rather than allowed to pass silently. Zero dates failed to parse.

**Titles.** Titles are free text, so they carry recruiter codes, hashtags, embedded salary snippets, emojis and inconsistent casing. A sequence of cleaning regex rules produced a normalised `title_clean`, with a fallback to the original if cleaning stripped a title to nothing. The original column was kept to ensure nothing was destroyed.

**Duplicates.** When a posting expires, employers repost the identical job. Treating same title + same company + same salary range as the same vacancy, and keeping the *earliest* posting, removed around 40% of rows (1.04M → 629k). Sorting by date before de-duplicating means the time series reflects when a role first appeared, not when it was last refreshed.

**Salaries: Repair before judging.** Instead of dropping inaccurate salaries or typos, we aimed to saved them. 
| Rule | Reading | Action |
|---|---|---|
| `salary_min_clean` < 100 | hourly rate | x hours x 52 / 12 (44.3h full-time, 21h part-time, MoM statistical averages) |
| min < 0.2 x max, max > 20,500 **AND** max/12 >= min | max quoted annually | max / 12 |
| min < 0.2 x max, max <= 20,500 | range too wide to trust | both set to the raw average |
| min > 40,000 | both quoted annually | both / 12 |

A set of unit-conversion rules repaired salaries quoted hourly or annually into a common monthly basis, applied in a deliberate order because an early fix can reveal what a later rule needs to see. Any outliers are not dropped and were flagged instead.

**The house pattern: flag, don't delete.** Throughout the notebook, a row with one bad field is never deleted. The untrusted *value* is blanked or a flag is set, and the row survives:

| Problem | Response |
|---|---|
| Seniority label contradicts years of experience | Blank the level, keep the posting |
| Senior role quoting an hourly-looking rate | Flag as a mismatch, do not convert |
| Salary too broken to repair | Leave it, flag it downstream |

The payoff is that a posting with a typo'd salary still counts toward hiring pool depth and industry demand. In essence, it is still a perfectly valid data and only the salary maths skips it.

**Filtering happens last.** Because the reliability filter runs *after* the unit conversions, thousands of postings that an early filter would have binned were rescued into the usable band. The final usable share for salary analysis is **99.6%** of postings, a net gain over filtering up front. 

**Industries and roles.** The JSON industry field was parsed defensively (a malformed row returns empty rather than crashing a 629k-row run) into list columns, then exploded into the long table — 43 distinct industries. Because the brief covers benchmarking by *role* as well as by industry, a second pass tagged each posting with role families derived from the cleaned title. Titles matching nothing are labelled "Other / Unclassified" rather than silently dropped.

**Analysis groupings.** Finally, continuous fields were bucketed into decision-friendly variables: experience bands, employment type groups, and posting month for the time series.

### Output

Two CSVs — one at posting grain, one at posting × industry grain — which the dashboard reads directly.

---

## 3. Dashboard Walkthrough (Streamlit)

The dashboard is a Streamlit app. The client picks a target industry or role and gets prevailing salary ranges plus a read on hiring pool depth.

### The counting rule

The app's central mechanic: filters match on the long table, then join surviving job posting IDs back to the posting-level table to count.

### Main Filters (sidebar)

| Control | Business question it serves |
|---|---|
| Date range | "What does the market look like over the most recent period?" |
| Industry (with Any / All logic) | "Show me IT" vs "show me roles sitting at the intersection of Finance *and* IT" |
| Role family | "Benchmark by job role type" |
| Title contains | Free-text search for niche roles the taxonomy doesn't name |
| Minimum postings for a benchmark | Suppresses thin slices so a median is never built on a handful of jobs |

The Any/All control is disabled until an industry is selected, so it cannot be used in a state where it means nothing. Date filtering uses the *original* posting date so a repost doesn't shift a job into a later month.

### Primary Main views

**Headline metric cards** — median salary, mean salary, number of hiring companies, average monthly vacancies. 

**Median** The median carries a delta against the whole-market median, so a client immediately sees whether their target industry pays above or below market. 

**Mean** The mean carries a delta against the median. When the mean sits higher, a tail of high-paying roles is pulling it up, which is itself a finding. 

Salary maths always runs on reliable rows regardless of the sidebar toggle, and the number of postings excluded is stated on screen.

### Secondary Charts

**Median salary by industry (top 10)** — built on the exploded table, because this chart is *supposed* to count per industry. 

**Position × employment heatmap** — colour is the median of each cell, not the maximum, so one outlier posting cannot light up a whole cell. Tooltips show the posting count behind each cell. 

**Salary spread by industry** — a scatter where each point is a posting. This shows the *distribution* behind each median, which a bar chart hides. For a client deciding an offer band, the spread matters more than the midpoint.

**Sunburst: industry → position level → employment type** — the structural shape of the market. 


### How it answers the business question

A user filtering to their target industry and seniority reads, in one screen: what the role pays relative to the wider market, how wide the range is, how many companies are hiring and how many postings the salary figure is actually based on.

---

## 4. Challenges & Learnings

### Challenges

- Salary fields are noisy. They include placeholders, and some recruiter postings carry very wide salary bands. 

- Titles are inconsistent. Recruiter codes, embedded salary snippets, location hints and formatting noise all appear in the title field, and different companies describe similar roles differently. This limits how reliably the actual skillsets required for a role can be inferred from the data.

- Time trend may reflect dataset coverage, not only market growth. Posting counts ramp sharply across initial months, which may reflect how the dataset was collected or retained rather than genuine market expansion. Separating real signal from extraction bias is not straightforward.

- Hiring difficulty is only a proxy. Repost count, days open, views and applications are *signals*, not direct proof. They suggest friction, but do not explain why a role is hard to fill. 

### Learnings

- Charting should follow the business question, not the other way around.
- Clean data is not the same as useful data.
- Making the effort to repair data can yield back valuable results.
- Converting raw fields into decision-friendly variables is where much of the analytical value comes from.
- Business dashboards need defensible assumptions, not just perfect data.
- Every simplification improves usability but introduces a tradeoff.
- Checking that grouped outputs still tie back to the raw data is essential.

### Possible next steps

- Validate the salary bands against an external benchmark to test whether our model's medians track actual offers.
- Improve the cleaning for `title`. Currently regex over titles not perfect, still a sizable "Other / Unclassified" bucket.
- Investigate the collection pattern behind the posting-volume ramp so the time series can be presented as trend.
- Explore and extend hiring-difficulty from a single proxy to a composite indicator, with the limitations stated on the dashboard itself.
