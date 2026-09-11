# Audio assets

Every asset in this directory needs a line here. No exceptions, no orphan files.

| File | Source | Licence |
|---|---|---|
| `sprite.mp3`, `sprite.ogg` | **Synthesised by `tools/make_audio.py`** | Ours outright, MIT with the rest of the repository |
| `sprite.json` | Generated alongside them | Ours outright |

## Why everything here is synthesised

The build spec is blunt about sourcing, and it is right to be. The actual
broadcast themes for Sunday and Monday night football are copyrighted
compositions owned by the networks. So are team fight songs, stadium anthem
recordings, and any commercially released track. None of them may be used, ripped,
or dropped in by a contributor.

What the spec does allow is *"commissioned or self-made original stings in that
brass-and-timpani idiom, which is a genre convention rather than a protected
work"*. So the fourteen sounds in the sprite are made here, out of oscillators and
noise: nothing is sampled, nothing is downloaded, and every asset is ours
outright. That is a far shorter conversation than auditing a folder of
CC0 downloads before a launch, and it means this file can never drift out of date.

Regenerate with:

```bash
python3 tools/make_audio.py
python3 tools/make_audio.py --preview horn_03   # one sound, as a wav, to listen to
```

## What is in the sprite

| Sound | Bus | Used for |
|---|---|---|
| `horn_01` | stings | An ordinary touchdown |
| `horn_02` | stings | A better one, and lead changes |
| `horn_03` | stings | Long touchdowns, clinches, hundred-point milestones |
| `trombone` | stings | Bench disasters and goose eggs. The sad one |
| `whoosh` | stings | Big plays |
| `riser` | stings | The red-zone countdown bed |
| `chime` | stings | Milestones |
| `doom` | stings | A manager below 5% while the opponent is still playing |
| `scratch` | stings | A drive that ends without a score |
| `buzzer` | stings | End of a game window |
| `crowd` | music | Under a big moment |
| `tap`, `flip`, `rip` | ui | Taps, card flips, the pack rip |

`tools/phrase_lint.py` fails if a phrase in `data/phrases/` names a sound that is
not in this sprite: a phrase pointing at a renamed or missing sound is silent at
play time and looks exactly like a phrase that simply was not chosen.

## The loop bed

`bed.mp3` / `bed.ogg` is an eight second pad, also synthesised here. Every
frequency in it is snapped to an integer multiple of 1/8 Hz so the loop is
seamless by construction, and the generator measures the join against the typical
sample step rather than against zero.

That measurement earned its place immediately. The first version compared the
first sample to the last and flagged a perfectly good loop, because wrapping from
the last sample to the first *is* one ordinary sample step and for a 55 Hz tone
that step is about 0.008. Rewritten to compare the join against the 99th
percentile of steps elsewhere, it then found a real fault: the one-pole lowpass
starts from zero state, so the first thirty samples came out attenuated and the
loop clicked every eight seconds. Filtering two copies and keeping the second
fixed it, from 8.9x a typical step to 0.93x.

Deliberately dull. It plays for four hours under everything else and its job is
to make silence feel like a room rather than a fault.

## Speech

The `commentary` bus is synthesised at runtime and cached by the hash of exactly
what is spoken, under `phrase/` (gitignored: it is derived, and a season of it is
a few hundred megabytes).

The spec asks for Piper, pre-rendered at deploy. Two things stop that being the
whole design: most lines carry a player's name, and a week's player universe is
not known until Sunday; and Piper is not installed on this machine, so the macOS
`say` binary stands in for local work. Rendering starts when the *server* picks
the line rather than when a phone asks for the file, which hides the latency
behind the SSE round trip and the browser's own request.

- [ ] **Install Piper on the droplet.** It is the shipping backend; `say` is
      macOS-only and exists so the pipeline is testable end to end here.
