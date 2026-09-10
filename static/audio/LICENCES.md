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

## Not yet made

- **A loop bed for the `music` bus.** The ducking machinery is built and wired,
  but with nothing playing on that bus it currently has nothing to duck. Needs a
  seamlessly loopable bed, which is a different synthesis problem to a sting.
- **Speech.** `commentary` is the one bus with no assets: Piper is not installed
  on this machine, so lines are currently displayed rather than spoken.
