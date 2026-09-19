# A/B results (recorded local run)

Facts copied from the lab's A/B summary. These are proxy measurements from one run, not a benchmark.

## Five-task comparison

| Task / metric | Without Jev | With Jev |
| --- | ---: | ---: |
| Google Flights browser opens | 1 | 0 (reused a Phase A JSON artifact) |
| News triage Jev calls | 0 | 1 |
| Flight-prep skills loaded | 6 | 3 |
| Same failing approach attempts | 3 | 0 after `stop_retry` |
| Model-research pages fetched | 10 | 4 |
| Model-research web searches | 5 | 2 |
| Jev calls | 0 | 1 for tasks where recorded |
| Recorded Jev estimate | $0 | $0.000175 |

The Grok Bot weekly included-usage meter was 37% at baseline, 38% after the first phase, and 39% after the second phase. Exact per-task Grok Bot tokens and exact Grok Bot dollar cost were not available, so the meter does not establish token savings.

## Jev 200k timing comparison

A separate 24-candidate filter used a shared collection step that was excluded from the arm ratio:

| Metric | Without Jev | With Jev |
| --- | ---: | ---: |
| Wall time | 53.803 s | 4.125 s |
| Pages fetched | 14 | 5 |
| Web searches | 2 | 0 |
| Jev calls | 0 | 2 |
| Jev input tokens | not applicable | 9,647 |
| Jev estimated cost | $0 | $0.000405 |
| Reported wall-time ratio | 1× | 13.0× |

The Jev arm fetched only the ranked top five, so it confirmed 5 qualifying hits versus 10 in the uncapped arm by design. That difference is not evidence that posts disappeared.

## Caveats

- The flight comparison with Jev reused an existing cache and was not a cold second search.
- Weekly usage includes surrounding chat overhead, not only these tasks.
- Jev router calls without returned usage were estimated at roughly 500 input tokens each in the source notes.
