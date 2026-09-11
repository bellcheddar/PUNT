# Writing phrases

Every line PUNT says comes from a file in this directory. Nothing is generated:
the commentator retrieves, it does not write. That is the whole design, and it is
why a line can never state a number that did not happen -- there is nowhere for an
invented one to come from.

One YAML file per Moment kind, named after it. 410 lines at the last count:

| File | Kind | Lines |
|---|---|---:|
| `touchdown.yaml` | `TOUCHDOWN` | 85 |
| `big_play.yaml` | `BIG_PLAY` | 67 |
| `filler.yaml` | `FILLER` | 50 |
| `bench.yaml` | `BENCH_DISASTER` | 44 |
| `doom.yaml` | `DOOM` | 35 |
| `lead_change.yaml` | `LEAD_CHANGE` | 35 |
| `milestone.yaml` | `MILESTONE` | 29 |
| `clinch.yaml` | `CLINCH` | 25 |
| `goose_egg.yaml` | `GOOSE_EGG` | 25 |
| `injury.yaml` | `INJURY` | 15 |

Run `python3 tools/phrase_lint.py` after editing. It catches a slot no Moment can
fill, a duplicate id, and a category thin enough that a line will come round twice
in one afternoon.

## One phrase

```yaml
- id: td_stack_03
  trigger: {kind: TOUCHDOWN, position: [WR, TE], delta_points: {gte: 12}}
  tone: [hype]
  roast_level: 1
  weight: 4
  cooldown: 900
  text: "{player} from distance. {delta} for {manager}."
  audio: horn
  voice: pbp
```

| Field | What it does |
|---|---|
| `id` | Unique across the whole bank. It is the cooldown key and it appears in the transcript, so make it say something. |
| `trigger` | Which Moments this line can answer. See below. |
| `tone` | Free-form labels for your own grouping. Nothing reads them at runtime. |
| `roast_level` | The sharpness gate. A line above the league's configured level is never eligible. 0 is safe, 1 is teasing, 2 is savage; `ROAST_LEVEL` in the environment clamps to that range and defaults to 1. |
| `weight` | Relative likelihood among everything else eligible. |
| `cooldown` | Seconds before this exact line may be used again. |
| `text` | The line, with `{slots}`. |
| `audio` | A sting from `static/audio`. |
| `voice` | `pbp` or `colour`. |

## Triggers

A trigger is a mapping of field to expected value. **Every** entry must match, so
adding one narrows the line.

`kind` is mandatory. Everything else is matched against the Moment's slots, plus
three fields read off the Moment itself: `magnitude` (0 to 1, how loud this was),
`delta_points`, and `win_prob_delta`.

Eight comparison forms, all of them supported:

| Form | Example | Matches when |
|---|---|---|
| scalar | `position: QB` | equal, compared as text |
| bool | `starter: true` | equal |
| list | `position: [WR, TE]` | the value is in the list |
| `gte` | `delta_points: {gte: 12}` | value >= 12 |
| `gt` | `delta_points: {gt: 12}` | value > 12 |
| `lte` | `quarter: {lte: 2}` | value <= 2 |
| `lt` | `quarter: {lt: 2}` | value < 2 |
| `in` | `status: {in: [OUT, DOUBTFUL]}` | membership, inside a range object |

Bounds combine in one object: `{gte: 3, lte: 6}` is a band. A range test against
something that is not a number answers "no" rather than raising, because a Moment
is allowed to be missing a field.

The shipped bank uses four of these eight. `gt`, `lt`, `in` and the bare list had
never been evaluated by anything until `test_every_trigger_form_works_including_the_ones_no_phrase_uses_yet`
was written, which is what this table is now checked against.

## Slots

`{player}`, `{manager}`, `{opponent}`, `{delta}` and `{kind}` are always
available. Everything else comes from that Moment's own context and therefore
varies by kind: `{total}`, `{position}`, `{slot}`, `{threshold}`, `{margin}`,
`{deficit}`, `{win_pct}`, `{started_points}`, `{status}`, and so on.

A line asking for a slot its Moment cannot fill is dropped rather than rendered
with a hole in it, so the failure is silence rather than "{total}" on the bar
screen. `phrase_lint.py` finds these at authoring time; look at what a real
Moment of that kind carries with:

```
python3 tools/timeline.py --kinds BENCH_DISASTER
```
