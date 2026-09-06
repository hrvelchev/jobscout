# Lanes: how postings are grouped, weighted and mapped to CV variants

Everything above the first `---` line is documentation and is ignored by the
parser. Sections are `## headings`; entries are `- items`. Copy to `lanes.md`
(gitignored) and adjust.

Lane names must match the lanes your scoring prompt uses:
ai, quant, data, energy, other.

- `lane_weights`: rank bonus per lane, format `lane = number`
- `lane_cv_map`: which CV file you attach for postings in this lane
- `dream_companies`: companies you watch — new postings get a rank boost
  and a [watched company] flag in the digest (normalized, lowercase)

---

## lane_weights

- ai = 5
- data = 3
- quant = 2
- energy = 1
- other = 0

## lane_cv_map

- ai = CV_backend.pdf
- data = CV_data.pdf
- quant = CV_data.pdf
- energy = CV_data.pdf
- other = CV_backend.pdf

## dream_companies

- exampletech
- initech
