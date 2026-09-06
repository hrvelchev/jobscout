# Sources: where postings are fetched from

Copy to `sources.md` (gitignored). Documentation above the `---` is ignored.

- `devbg_categories`: dev.bg job-board category slugs. The fetcher requests
  `https://dev.bg/company/jobs/<slug>/` once per scout run, politely
  (one request per category, 10s apart, ETag-aware).
- `greenhouse_boards`: Greenhouse board tokens for companies you watch.
  The public JSON API is `https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`
  Find the token in the company careers URL (e.g. job-boards.eu.greenhouse.io/<token>).

---

## devbg_categories

- python
- data-science

## greenhouse_boards

- examplecompany
