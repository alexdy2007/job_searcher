# UK Data Source Candidates

Seeding run (first pass) — 2026-09-22. Segments worked: **UK fintech/banking**
and **UK health/biotech**. Two sources pre-existed (Figma, GitLab, both
unrelated to these segments) and were not touched.

Status legend: `added` (verified + row created), `pending-adapter` (ATS known
and token found, but no registered adapter yet — Lever/Ashby/Workable),
`no-public-board` (no board on a trackable ATS; uses Workday/SmartRecruiters/
custom or nothing found), `rejected` (board probed but doesn't clear the bar;
reason in Notes), `lead` (name/signal found, not yet resolved to a token or
not yet probed).

## Candidates

| Company | Segment | Careers URL | ATS | Token | Status | UK data roles seen | Checked | Notes |
|---|---|---|---|---|---|---|---|---|
| Monzo | fintech/banking | https://monzo.com/careers | greenhouse | `monzo` | added | 14: Director of Data (Payments), Data Science Manager ×2 (incl. Financial Crime), Lead Data Scientist, Lead ML Scientist ×4 (Business Banking, Customer Operations, FinCrime, Search), Senior ML Manager (Borrowing), ML Tech Lead, Credit Model Validation Manager (ML), Senior Analytics Engineer, Staff Analytics Engineer, FP&A Data & Analytics Manager — all Cardiff/London/Remote (UK) | 2026-09-22 | Probed OK, 66 jobs total. Corrected on review: the seeding run recorded 4, the board actually carries 14. Easily the strongest UK data employer found this pass. Also 1 Senior Data Scientist in Barcelona (not counted). |
| GoCardless | fintech/banking | https://gocardless.com/about/careers | greenhouse | `gocardless` | added | 1: Senior Data Scientist, Payment Intelligence — London, UK | 2026-09-22 | Probed OK, 26 jobs total. Data Analyst/Junior Data Analyst roles also open but in Lisbon/Riga, not UK — not counted toward the bar (and wouldn't qualify alone regardless). |
| Isomorphic Labs | health/biotech | https://www.isomorphiclabs.com/careers | greenhouse | `isomorphiclabs` | added | 4: Data Platform Engineer (London), ML Research Engineer (London), Research Scientist – Applied LLMs (London), Research Scientist – Machine Learning (London) | 2026-09-22 | Probed OK, 29 jobs total. Alphabet/DeepMind-spinout AI drug-design company, London HQ. Strong signal — Talent Partner (Data) role also open, indicating a growing data org. |
| Liberis | fintech/banking | https://www.liberis.com/careers | greenhouse | `liberis` | added | 1: Senior Data Product Manager — London, United Kingdom | 2026-09-22 | Probed OK, 16 jobs total. Embedded-finance/SME lending fintech (London, Nottingham + international). Thin data signal (one role) but qualifies — Data Product Manager is in scope and board is otherwise healthy. Worth rechecking for engineering roles next pass. |
| Capital on Tap | fintech/banking | https://www.capitalontap.com/careers | greenhouse | `capitalontap` | added | 2: Data Engineer (London), Data Scientist – Growth Team (London) | 2026-09-22 | Probed OK, 37 jobs total. Business credit card fintech, London HQ. |
| Tide | fintech/banking | https://www.tide.co/careers/ | greenhouse | `tide` | rejected | 0 current UK roles | 2026-09-22 | Probed OK, board resolves (83 jobs), so **not** `no-public-board`. Only current data-adjacent roles are Manager, AML Analytics (Hyderabad) and Manager, Analytics, Fraud (Bulgaria) — neither UK, neither an engineering/science role. A "Lead Data Engineer (Snowflake/DBT)" posting surfaced in search but its job URL now redirects to `/tide?error=true` (closed). Revisit next run — Tide is a data-heavy fintech and likely to repost. |
| TrueLayer | fintech/banking | https://truelayer.com/careers/ | greenhouse | `truelayer` | rejected | 0 | 2026-09-22 | Probed OK, board resolves but only 2 jobs open total, none data-related. Open Banking API company — plausible future data hiring. Revisit next run. |
| Revolut | fintech/banking | https://www.revolut.com/careers/ | — | — | no-public-board | — | 2026-09-22 | No Greenhouse/Lever/Ashby/Workable board found via search. Careers site appears custom-built. |
| Wise | fintech/banking | https://wise.jobs/ | smartrecruiters | — | no-public-board | — | 2026-09-22 | Runs on SmartRecruiters (`jobs.smartrecruiters.com/Wise/...`), not one of the four tracked ATS kinds. Confirmed multiple London Data Scientist roles exist (Fraud, Growth & Strategic Finance) but board isn't scrapable under current adapters. |
| Barclays | fintech/banking | https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays | workday | — | no-public-board | — | 2026-09-22 | Workday, not tracked. |
| AstraZeneca | health/biotech | https://careers.astrazeneca.com/ | workday | — | no-public-board | — | 2026-09-22 | Workday, not tracked. Multiple Data Scientist roles exist globally but not scrapable under current adapters. |
| Currencycloud | fintech/banking | https://www.currencycloud.com/company/careers/ | — | — | no-public-board | — | 2026-09-22 | No trackable ATS board found; appears to have very few current openings regardless. |
| Starling Bank | fintech/banking | https://www.starlingbank.com/careers/ | workable | `starling-bank` | pending-adapter | Database Engineer (DBA), Staff Data Engineer (Engine by Starling) — both UK | 2026-09-22 | Token read from `apply.workable.com/starling-bank/j/...`. Not verified (no registered Workable adapter) — do not add. |
| Zopa | fintech/banking | https://careers.zopa.com/ | lever | `zopa` | pending-adapter | Analyst – Fraud Analytics seen; no engineering/science role confirmed yet | 2026-09-22 | Token read from `jobs.lever.co/zopa/...`. Not verified — do not add. Recheck for a Data Engineer/Scientist role next pass before treating as a strong candidate. |
| OakNorth Bank | fintech/banking | https://oaknorth.co.uk/jobs/ | lever | `oaknorth.ai` | pending-adapter | Has a "Data and Analytics" department on the board; specific role titles not itemized this pass | 2026-09-22 | Token read from `jobs.lever.co/oaknorth.ai`. AI-driven credit/lending bank — plausible strong candidate. Not verified — do not add. |
| Marshmallow | fintech/banking | https://www.marshmallow.com/jobs | workable | `marshmallow` | pending-adapter | Data Science Manager (Pricing) seen | 2026-09-22 | Token read from `apply.workable.com/marshmallow/`. UK motor insurer (London). Not verified — do not add. |
| Zilch | fintech/banking | https://www.zilch.com/uk/careers/ | ashby | `zilch` | pending-adapter | Not itemized this pass — board exists, roles not read | 2026-09-22 | Token read from `jobs.ashbyhq.com/zilch`. UK BNPL fintech (London). Not verified — do not add. Needs a role-level check next run before counting toward evidence. |
| Quantexa | fintech/banking | https://www.quantexa.com/careers/ | ashby / workable | `quantexa` (both) | pending-adapter | Data Engineer role seen (location: Melbourne, not UK) via cached listing; UK-specific data role not yet confirmed | 2026-09-22 | Both `jobs.ashbyhq.com/quantexa` and `apply.workable.com/quantexa` found — company appears to run both. UK HQ (London), decision-intelligence/data company — strong prior, but needs a UK-located data role confirmed next run before it's more than a lead. |
| ClearBank | fintech/banking | https://clearbank.co.uk/careers/ | workable | unclear — `jobs.workable.com/company/bBykc5fhDGtsJfMSSBiaCn/jobs-at-clearbank`, not the standard `apply.workable.com/<token>` form | lead | Data Engineer – Regulatory Reporting, Analyst Data Engineer seen via cached third-party listings (builtin.com), not confirmed live | 2026-09-22 | Nonstandard Workable URL — token not cleanly extracted. Needs direct fetch of the careers page next run to resolve the real board token. |
| Cleo | fintech/banking | https://www.cleo.com/careers | greenhouse | `cleo` (note: page slug shows `cleoai`, API token is `cleo`) | lead | Marketing Data Scientist (London) seen via cached search result | 2026-09-22 | Confirmed token `cleo` resolves (HTTP 200) but full job list not yet pulled/probed this pass — do not add until probed and a current UK data role is confirmed via `sources probe`. |
| Checkout.com | fintech/banking | https://www.checkout.com/careers | — | — | lead | — | 2026-09-22 | No board token found this pass; known large London fintech, worth a direct site fetch next run. |
| Thought Machine | fintech/banking | https://www.thoughtmachine.net/careers/ | — | — | lead | — | 2026-09-22 | No GH/Lever board surfaced in search; London core-banking-platform company, worth a direct site fetch next run. |
| Freetrade | fintech/banking | — | — | — | lead | — | 2026-09-22 | Name surfaced, no board found this pass. |
| Plum | fintech/banking | https://www.withplum.com/careers | — | — | lead | Senior Data Engineer role referenced via cached listing (builtin.com) | 2026-09-22 | ATS/token not confirmed this pass — direct site fetch needed. |
| Funding Circle | fintech/banking | https://careers.fundingcircle.com/ | — | — | lead | Data Engineer, London — referenced via startup.jobs/cached listings; multiple engineering roles implied | 2026-09-22 | ATS not confirmed — search conflated with an unrelated company ("Circle.so"). Direct site fetch needed next run to find the real board/token. |
| Huma | health/biotech | https://www.huma.com/careers | — | — | lead | — | 2026-09-22 | UK (London) health-tech (remote patient monitoring). No board found this pass — search results were mostly noise (unrelated "Humana", "Interwell Health"). Direct site fetch needed. |
| Exscientia | health/biotech | https://www.exscientia.ai/careers | — | — | lead | — | 2026-09-22 | Oxford AI drug-discovery company. No board confirmed this pass — search results were noise. Direct site fetch needed. |
| Curve Analytics | — | curveanalytics.co.uk | — | — | rejected | — | 2026-09-22 | False positive — unrelated UK consultancy, not the Curve fintech card company. Discard; do not confuse with "Curve" (curve.com) in future runs. |

## Pending-adapter tally

Companies with a known token but no registered adapter, which is the evidence
for which adapter Phase 2 should build first:

| ATS | Clean tokens | Companies |
|---|---|---|
| Workable | 3 | Starling Bank, Marshmallow, Quantexa (ClearBank found but token unresolved) |
| Lever | 2 | Zopa, OakNorth Bank |
| Ashby | 2 | Zilch, Quantexa (also on Workable) |

**Do not treat this as a decision yet.** 3–2–2 across 28 companies from a
single segment is inside the noise, and this pass was fintech-heavy by
construction, so it measures which ATS UK fintechs favour rather than which
ATS the UK data market favours. Quantexa is double-counted because it appears
on both Workable and Ashby. Re-tally after health/biotech and at least one
more segment are worked before letting this drive the build order.

## Search queries run this pass

ATS-targeted (`site:` searches):
- `site:job-boards.greenhouse.io "Data Engineer" London fintech`
- `site:boards.greenhouse.io "Analytics Engineer" "United Kingdom"`
- `site:jobs.lever.co "Data Scientist" London fintech`
- `site:job-boards.greenhouse.io "Data Scientist" London biotech`
- `site:boards.greenhouse.io "Data Engineer" "United Kingdom" health`

Company-targeted (fintech/banking):
- Revolut, Monzo, Wise, Starling Bank, Barclays, Zopa, Tide, GoCardless, Cleo,
  ClearBank, Thought Machine, Checkout.com, Currencycloud/TrueLayer,
  OakNorth Bank, Freetrade/Curve/Plum, Marshmallow/Zilch/Bud, Quantexa,
  Funding Circle — each searched as `"<Company> careers data <role> greenhouse
  OR lever OR ashby OR workable board [token]"`.

Company-targeted (health/biotech):
- AstraZeneca, Huma, Exscientia — same pattern as above.

Untouched this pass — good starting points for the next run: GSK, Babylon
Health (status uncertain), BenevolentAI, Owlstone Medical, Congenica,
Genomics England, Sensyne Health, Immunocore, Oxford Nanopore Technologies,
Healx, Kheiron Medical (health/biotech); HSBC, Lloyds, NatWest, Santander UK,
Nationwide (large banks, likely Workday — low expected yield but unconfirmed),
Codat, Modulr, iwoca, ComplyAdvantage, Onfido, G-Research, Man Group (fintech).

Also worth doing next run: re-run the `site:` ATS sweep for Lever and Ashby
specifically (this pass leaned Greenhouse-heavy on the site: queries — most
Lever/Ashby/Workable hits came from company-targeted search instead), and vary
city terms (Manchester, Edinburgh, Cambridge, Leeds, Belfast) which were
barely used this pass.
