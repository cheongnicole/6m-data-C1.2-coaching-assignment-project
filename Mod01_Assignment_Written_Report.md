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

**Flag, don't delete.** Throughout the notebook, a row with one bad field is never deleted. The untrusted *value* is blanked or a flag is set, and the row survives:

| Problem | Response |
|---|---|
| Seniority label contradicts years of experience | Blank the level, keep the posting |
| Senior role quoting an hourly-looking rate | Flag as a mismatch, do not convert |
| Salary too broken to repair | Leave it, flag it downstream |

The payoff is that a posting with a typo'd salary still counts toward hiring pool depth and industry demand. In essence, it is still a perfectly valid data and only the salary maths skips it.

**Filtering happens last.** Because the reliability filter runs *after* the unit conversions, thousands of postings that an early filter would have binned were rescued into the usable band. The final usable share for salary analysis is **99.6%** of postings, a net gain over filtering up front. 

**Industries and roles.** The JSON industry field was parsed defensively (a malformed row returns empty rather than crashing a 629k-row run) into list columns, then exploded into the long table — 43 distinct industries. Because the brief covers benchmarking by **role** as well as by industry, a second pass tagged each posting with role families derived from the cleaned title. Titles matching nothing are labelled "Other / Unclassified" rather than silently dropped.

**Analysis groupings.** Finally, continuous fields were bucketed into decision-friendly variables: experience bands, employment type groups, and posting month for the time series.

### Output

Two CSVs — one at posting grain, one at posting × industry grain — which the dashboard reads directly.

---

## 3. Dashboard Walkthrough (Streamlit)

The dashboard is a Streamlit app built around a single question: **if we hire in Singapore, where should we hire, what will it cost, and how hard will it be?** 

### Design principle: three questions, three tabs

The header states the purpose in plain language:

| Tab | Question it answers |
|---|---|
| **Where demand sits** | Which sectors and job families are actually hiring and over what period |
| **What to budget** | What the role costs: by sector, by experience band and whether that has moved over time |
| **Where hiring looks tighter** | Which sectors and roles attract the weakest candidate response, i.e. where hiring will be hardest |
| **Filtered data** | The underlying postings |


A shared sidebar filters every tab at once, so the user carries one consistent market definition across all three questions.

### Overview mode and comparison mode

The app runs in **two modes**, and switches automatically based on how narrow the filters are.

- **Overview mode** (no sector or job-family filter): rank and compare. Which sector leads demand, which pays most, which is tightest.
- **Comparison mode** (one sector, job family, title search, employment type or experience band selected): stop ranking, start benchmarking. The selected slice is named as a **scope** and measured against the wider market for the same date range.

The scope is written at the top of the page 
> "all roles in view", "roles in Information Technology", "Data / Analytics roles" 

followed by filter badges and a summary sentence stating how the scope compares on pay, applications per vacancy, application rate, and share of captured postings. 

Each panel has a defined comparison-mode form:

| Panel | Overview mode | Comparison mode |
|---|---|---|
| Sector demand | Top 10 sectors ranked | Donut showing the scope's share of captured market demand |
| Demand over time | One volume line | Scope line against the largest peer sectors |
| Experience mix | Volume by band | Dumbbell comparing the scope's mix against the market's |
| Sector pay | Top 10 sectors by median pay | Scope marker against peer sectors, with the market median as a dashed reference |
| Pay by experience | Volume bars + pay line | Locked to the selected band; the peer-sector pay trend takes over |
| Hiring map | Scatter of all sectors on pay vs response | Scope's rank position among sectors on tightness |
| Role response | Weakest job families ranked | Within a single sector, which experience bands draw fewest applicants |

The pattern is consistent: **when a chart's grouping dimension collapses to one value, the chart changes dimension rather than degrading.** Either it drills to the next level down (sector to experience band), or it switches from ranking to benchmarking against the market.

### Headline strip

Six metric cards sit above the tabs: 
- postings in view
- hiring companies
- median monthly salary
- applications per vacancy
- application rate
- share of market

Each with a delta against the whole dataset. Above them, a one-sentence summary states which sector leads the filtered view, what it pays, and whether candidate response is stronger, weaker or in line with the wider market. 

### Measuring hiring difficulty

The dashboard uses **candidate response**: applications per vacancy, and applications as a share of views.

These feed a **sector hiring tightness score.** A weighted rank combining low applications per vacancy, low application rate, and high median pay which is presented as a four-level label (Easier / Balanced / Tighter / Tightest) rather than a raw number. 

A **sector × experience-band heatmap** carries the same measure across two dimensions at once, so difficulty can be read as a combination rather than a single ranking. A sector may be easy to staff at entry level and hard at ten years. When one sector is selected the rows switch to role families within it, following the same drill-down rule as the rest of the app. Its heading is generated from the data, naming the thinnest and deepest cells rather than labelling the chart, so the finding is stated before the reader interprets the colours.

### Guardrails carried into the design

- **Minimum postings per benchmark:** (slider, default 100) No median or response figure is drawn on a thin slice.
- **Salary-clean rows only:** (on by default) Pay maths runs on the reliable subset while demand counts use every posting
- **Coverage shading on the time series:** Months before the volume ramp are shaded and captioned as likely collection anomaly rather than market contraction. The chart title says "becomes more reliable from March 2023 onward" instead of implying growth.
- **Salary trend split by experience band:** Rather than one overall line, because a single trend would move with the seniority mix rather than with pay.
- **Unclassified roles excluded from the role comparison:** Roles carrying a usable role label stated on screen.

### The narrative structure: Context → Finding → Decision

Each tab is written to carry the reader through **three acts**, with the chart living in the middle:

**Tab 1 — Where demand sits**

- **Context:** You are entering a market of 626,968 postings and 53,060 hiring companies
- **Finding:** Demand is concentrated where Information Technology leads with most postings, and the market leans mid-level, with 2–4 years the largest experience band by a wide margin.
- **Decision:** Your entry team will be competing hardest in the sectors and seniority bands where everyone else is already hiring. **Do you hire where the pool is deepest, or where it is quieter?**

**Tab 2 — What to budget**

- **Context:** Headcount is the largest line item in an entry budget, and a single market-wide average hides the thing you are actually budgeting for.
- **Finding:** Median monthly pay ranges from around S$4,000 in Admin/Secretarial to S$6,500 in Sales/Business Dev, and seniority moves the number far more than sector 
- **Decision:** Team shape drives the budget more than sector choice. The question is **whether one senior hire or three mid-level hires better fits the entry plan.**

**Tab 3 — Where hiring looks tighter**

- **Context:** A sector that pays well is not necessarily easy to staff, and a cheap sector is not necessarily easy either.
- **Finding:** Information Technology and Banking and Finance sit among the highest-paying sectors at around S$7,000 median, yet applicant response splits sharply within them. Software Engineering roles draw about 1.45 applications per vacancy against a market average of 2.45, while IT / Infrastructure roles in the same sector draw around 2.40. The same pay band, very different applicant depth.
- **Decision:** Attracting applicants is not the constraint in this scope, the budget is. The question is **whether the premium buys enough capability to justify hiring here rather than in a cheaper adjacent sector.**

Act 3 deliberately stops at the choice. The dashboard's job is to make the comparison unavoidable; the decision belongs to the client carrying the risk.

---

## 4. Challenges & Learnings

### Challenges

- Salary fields are noisy. They include placeholders, and some recruiter postings carry very wide salary bands. 

- Titles are inconsistent. Recruiter codes, embedded salary snippets, location hints and formatting noise all appear in the title field, and different companies describe similar roles differently. This limits how reliably the actual skillsets required for a role can be inferred from the data.

- Sector labels do not always describe the work. Information Technology postings mix building roles with the sales, admin and support roles. The dashboard handles this with a declared core-tech subset, which works but is a hand-picked exception rather than a rule the data produced.

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
