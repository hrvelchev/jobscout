# Prefilter: free rules that kill postings BEFORE any paid LLM call

Copy to `filters.md` (gitignored). Documentation above the `---` is ignored.

- `exclude_patterns`: lowercase substrings; a posting whose title or
  description contains any of them is dropped (verdict: excluded).
- `required_keywords`: a posting must contain AT LEAST ONE of these
  (title or description, lowercase) to survive (verdict: no_lane_kw).
  Keep this broad — it is a cost gate, not the ranking.

---

## exclude_patterns

- php developer
- wordpress developer
- .net developer
- internship

## required_keywords

- python
- data engineer
- machine learning
- backend
- sql
