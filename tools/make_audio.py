#!/usr/bin/env python3
"""Synthesise the sting sprite from scratch.

The build spec is blunt about sourcing: the actual broadcast themes are
copyrighted compositions owned by the networks, and so are team fight songs and
stadium anthem recordings. What it does allow is "commissioned or self-made
original stings in that brass-and-timpani idiom, which is a genre convention
rather than a protected work".

So these are made here, out of oscillators and noise. Nothing is sampled, nothing
is downloaded, and every asset in the repository is therefore ours outright --
which is a far shorter conversation than auditing a folder of Freesound files
before a launch.

    python3 tools/make_audio.py            # writes static/audio/
    python3 tools/make_audio.py --preview horn_03

Output is one sprite plus a Howler-format offset map. Twenty separate fetches on
a bar's shared wifi will not work; one sprite will.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "audio"
RATE = 44_100

#: Silence between sounds in the sprite. Howler seeks to a millisecond offset and
#: stops at a duration, and a sound that runs into its neighbour on a slow seek
#: is the classic sprite artefact. 300 ms is generous and costs 13 kB.
GAP = 0.30
#: Silence appended to every sound, longer than the 8 ms the sprite window trims
#: off the end. See the assembly loop for what happens when it is not.
TAIL = 0.014


# --------------------------------------------------------------------------
# oscillators and shaping
# --------------------------------------------------------------------------

def t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * RATE)) / RATE


def sine(freq, seconds: float, phase: float = 0.0) -> np.ndarray:
    time = t(seconds)
    f = np.full_like(time, freq, dtype=float) if np.isscalar(freq) else np.asarray(freq)[: len(time)]
    return np.sin(2 * np.pi * np.cumsum(f) / RATE + phase)


def saw(freq, seconds: float) -> np.ndarray:
    """Band-limited enough for this: a summed harmonic series, which avoids the
    aliasing buzz a naive ramp gives at these fundamentals."""
    time = t(seconds)
    f = np.full_like(time, freq, dtype=float) if np.isscalar(freq) else np.asarray(freq)[: len(time)]
    phase = 2 * np.pi * np.cumsum(f) / RATE
    out = np.zeros_like(time)
    harmonic = 1
    while harmonic * float(np.max(f)) < RATE / 2.2 and harmonic <= 18:
        out += np.sin(harmonic * phase) / harmonic
        harmonic += 1
    return out * 0.55


def noise(seconds: float, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(int(seconds * RATE))


def adsr(n: int, attack=0.01, decay=0.1, sustain=0.7, release=0.3) -> np.ndarray:
    """A plain envelope. `attack` short and `release` long is the brass shape."""
    a, d, r = int(attack * RATE), int(decay * RATE), int(release * RATE)
    s = max(0, n - a - d - r)
    return np.concatenate([
        np.linspace(0, 1, a, endpoint=False),
        np.linspace(1, sustain, d, endpoint=False),
        np.full(s, sustain),
        np.linspace(sustain, 0, n - a - d - s),
    ])[:n]


def lowpass(x: np.ndarray, cutoff) -> np.ndarray:
    """One-pole, with a cutoff that may sweep. Not a filter anybody would ship in
    a plugin; entirely adequate for taking the fizz off a saw stack."""
    cutoff = np.full(len(x), cutoff, dtype=float) if np.isscalar(cutoff) else np.asarray(cutoff)[: len(x)]
    alpha = 1 - np.exp(-2 * np.pi * np.clip(cutoff, 20, RATE / 2.2) / RATE)
    out = np.empty_like(x)
    y = 0.0
    for i, (sample, a) in enumerate(zip(x, alpha)):
        y += a * (sample - y)
        out[i] = y
    return out


def highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    return x - lowpass(x, cutoff)


def normalise(x: np.ndarray, peak: float = 0.82) -> np.ndarray:
    top = float(np.max(np.abs(x))) or 1.0
    return x / top * peak


def fade(x: np.ndarray, ms: float = 6.0) -> np.ndarray:
    """Both ends, always. A sprite cut on a non-zero sample clicks, and a click
    is the one artefact everybody in the room hears."""
    n = int(ms / 1000 * RATE)
    if len(x) < 2 * n:
        return x
    ramp = np.linspace(0, 1, n)
    if x.ndim == 2:
        ramp = ramp[:, None]
    x = x.copy()
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


# --------------------------------------------------------------------------
# the stings
# --------------------------------------------------------------------------

def brass(root: float, seconds: float, chord=(1.0, 1.5, 2.0), bright=5200, dark=900,
          attack=0.012, sustain=0.75) -> np.ndarray:
    """A brass stab: a stack of detuned saws under a closing lowpass.

    The falling cutoff is what makes it read as brass rather than as a synth
    chord -- real brass loses its upper harmonics as the note settles.
    """
    n = int(seconds * RATE)
    layers = np.zeros(n)
    for i, ratio in enumerate(chord):
        for detune in (-4.0, 0.0, 4.5):
            layers[: n] += saw(root * ratio + detune, seconds)[:n] / (i + 1.4)
    cutoff = np.geomspace(bright, dark, n)
    voiced = lowpass(layers, cutoff)
    return normalise(voiced * adsr(n, attack, 0.10, sustain, seconds * 0.45))


def timpani(root: float, seconds: float) -> np.ndarray:
    """A struck drum: a fast downward pitch bend plus a noise transient."""
    n = int(seconds * RATE)
    bend = np.geomspace(root * 1.9, root, n)
    body = sine(bend, seconds) + 0.4 * sine(bend * 1.5, seconds)
    hit = lowpass(noise(0.03, seed=7), 2400) * np.linspace(1, 0, int(0.03 * RATE))
    out = body[:n] * adsr(n, 0.002, 0.05, 0.35, seconds * 0.7)
    out[: len(hit)] += hit * 0.8
    return normalise(out, 0.9)


def sound_horn_01() -> np.ndarray:
    return fade(brass(233.08, 0.62))                      # B flat


def sound_horn_02() -> np.ndarray:
    """Two stabs. The second is a fourth up, which is the shape of every sports
    fanfare ever written and is why it reads as one."""
    first, second = brass(233.08, 0.30), brass(311.13, 0.62)
    out = np.zeros(int(0.92 * RATE))
    out[: len(first)] += first
    start = int(0.30 * RATE)
    out[start : start + len(second)] += second[: len(out) - start]
    return fade(normalise(out))


def sound_horn_03() -> np.ndarray:
    """The big one: three rising stabs over a timpani hit."""
    out = np.zeros(int(1.65 * RATE))
    for offset, root, length in ((0.00, 233.08, 0.26), (0.24, 311.13, 0.26), (0.48, 466.16, 1.05)):
        stab = brass(root, length, bright=6200)
        start = int(offset * RATE)
        out[start : start + len(stab)] += stab[: len(out) - start] * 0.8
    drum = timpani(58.27, 1.0)
    out[: len(drum)] += drum * 0.55
    return fade(normalise(out))


def sound_trombone() -> np.ndarray:
    """The sad one. A descending gliss with a wobble on the way down; the wobble
    is what makes it comic rather than merely gloomy."""
    seconds = 1.45
    n = int(seconds * RATE)
    slide = np.geomspace(311.13, 116.54, n)
    wobble = 1 + 0.02 * np.sin(2 * np.pi * 5.5 * t(seconds))
    voiced = saw(slide * wobble[:n], seconds)[:n] + 0.5 * saw(slide[:n] * 2, seconds)[:n]
    voiced = lowpass(voiced, np.geomspace(3200, 500, n))
    return fade(normalise(voiced * adsr(n, 0.05, 0.2, 0.8, 0.5)))


def sound_whoosh() -> np.ndarray:
    """Filtered noise swept upwards: the sound of something moving past."""
    seconds = 0.50
    n = int(seconds * RATE)
    swept = lowpass(noise(seconds, seed=1), np.geomspace(400, 7000, n))
    swept = highpass(swept, 300)
    return fade(normalise(swept * adsr(n, 0.05, 0.1, 0.8, 0.3), 0.6))


def sound_riser() -> np.ndarray:
    """The red-zone countdown bed: pitch and noise both climbing, so the tension
    is in two places at once and the release has somewhere to fall from."""
    seconds = 1.9
    n = int(seconds * RATE)
    tone = sine(np.geomspace(180, 900, n), seconds)[:n]
    air = highpass(lowpass(noise(seconds, seed=3), np.geomspace(900, 9000, n)), 600)
    ramp = np.linspace(0, 1, n) ** 1.8
    return fade(normalise((tone * 0.6 + air * 0.5) * ramp, 0.75))


def sound_chime() -> np.ndarray:
    """A bell for milestones: inharmonic partials, fast decay."""
    seconds = 1.1
    n = int(seconds * RATE)
    out = np.zeros(n)
    for ratio, gain, decay in ((1.0, 1.0, 1.0), (2.76, 0.5, 0.6), (5.40, 0.25, 0.35), (8.93, 0.12, 0.2)):
        partial = sine(880 * ratio, seconds)[:n]
        out += partial * gain * np.exp(-np.linspace(0, 9 / decay, n))
    return fade(normalise(out, 0.7))


def sound_doom() -> np.ndarray:
    """A low minor cluster that sags. Played once per doomed manager, so it can
    afford to be slow."""
    seconds = 2.1
    n = int(seconds * RATE)
    sag = np.geomspace(1.0, 0.94, n)
    out = np.zeros(n)
    for root in (58.27, 69.30, 87.31):                    # B flat, D flat, F
        out += saw(root * sag, seconds)[:n] / 3
    out = lowpass(out, np.geomspace(1400, 260, n))
    drum = timpani(43.65, 1.4)
    out[: len(drum)] += drum * 0.4
    return fade(normalise(out * adsr(n, 0.08, 0.4, 0.75, 0.9), 0.75))


def sound_scratch() -> np.ndarray:
    """A record scratch: a narrow noise band whose centre frequency is dragged
    back and forth."""
    seconds = 0.48
    n = int(seconds * RATE)
    drag = 2600 + 2100 * np.sin(2 * np.pi * 6.5 * t(seconds)) ** 3
    band = lowpass(noise(seconds, seed=5), drag[:n])
    band = highpass(band, 700)
    return fade(normalise(band * adsr(n, 0.01, 0.05, 0.85, 0.12), 0.7))


def sound_buzzer() -> np.ndarray:
    seconds = 0.8
    n = int(seconds * RATE)
    buzz = np.sign(sine(138.59, seconds)) * 0.5 + saw(277.18, seconds)[:n] * 0.5
    return fade(normalise(lowpass(buzz[:n], 2200) * adsr(n, 0.005, 0.02, 0.95, 0.1), 0.7))


def sound_crowd() -> np.ndarray:
    """A crowd swell. Pink-ish noise with a slow envelope and a little movement,
    used under a big moment rather than on its own."""
    seconds = 2.2
    n = int(seconds * RATE)
    body = lowpass(noise(seconds, seed=11), 1800)
    body = highpass(body, 180)
    movement = 1 + 0.25 * np.sin(2 * np.pi * 0.7 * t(seconds))[:n]
    swell = np.concatenate([np.linspace(0, 1, int(n * 0.35)) ** 2,
                            np.linspace(1, 0, n - int(n * 0.35)) ** 1.4])
    return fade(normalise(body * movement * swell, 0.55))


def sound_tap() -> np.ndarray:
    seconds = 0.06
    n = int(seconds * RATE)
    click = highpass(noise(seconds, seed=13), 1800) * np.exp(-np.linspace(0, 14, n))
    return fade(normalise(click, 0.45), 2)


def sound_flip() -> np.ndarray:
    """A card turning over: a short noise flick with a rising then falling band."""
    seconds = 0.20
    n = int(seconds * RATE)
    arc = np.concatenate([np.geomspace(900, 5200, n // 2), np.geomspace(5200, 1200, n - n // 2)])
    flick = lowpass(noise(seconds, seed=17), arc)
    return fade(normalise(flick * np.exp(-np.linspace(0, 5, n)), 0.55), 3)


def sound_rip() -> np.ndarray:
    """Foil tearing: a burst of high noise, amplitude-modulated hard so it reads
    as many small tears rather than one hiss."""
    seconds = 0.72
    n = int(seconds * RATE)
    grain = np.abs(np.sin(2 * np.pi * 52 * t(seconds)))[:n] ** 0.4
    tear = highpass(noise(seconds, seed=19), 1500) * grain
    return fade(normalise(tear * adsr(n, 0.01, 0.15, 0.7, 0.3), 0.6))


#: The loop bed's length. Every frequency in it is snapped to an integer
#: multiple of 1/BED_SECONDS, which is what makes the loop seamless: a component
#: that does not complete a whole number of cycles leaves a step at the join, and
#: a step in a bed playing for four hours is a click every eight seconds.
BED_SECONDS = 8.0


def snap(freq: float) -> float:
    """The nearest frequency that completes a whole number of cycles per loop."""
    return max(1, round(freq * BED_SECONDS)) / BED_SECONDS


def sound_bed() -> np.ndarray:
    """The music bed: a low, sparse pad with a soft pulse, made to loop forever.

    Deliberately dull. It plays for four hours under everything else and its
    whole job is to make silence feel like a room rather than a fault, so it
    carries no melody and nothing that could become annoying on the fiftieth
    repeat.
    """
    n = int(BED_SECONDS * RATE)
    out = np.zeros(n)

    # A1 and its fifths, all snapped. Tremolo rates are snapped too, or the
    # amplitude envelope itself would not line up at the join.
    for freq, gain in ((55.0, 1.0), (82.5, 0.45), (110.0, 0.5), (165.0, 0.22), (220.0, 0.14)):
        tone = sine(snap(freq), BED_SECONDS)[:n]
        tremolo = 1 + 0.16 * np.sin(2 * np.pi * snap(0.25) * t(BED_SECONDS))[:n]
        out += tone * tremolo * gain

    # Filtered over two copies, keeping the second. `lowpass` is an IIR whose
    # state starts at zero, so the first thirty samples come out attenuated --
    # which at the loop point is a step, and a step every eight seconds for four
    # hours is a click track. Pre-rolling one whole loop through the filter means
    # the kept half begins in steady state, exactly as it will when looping.
    out = lowpass(np.concatenate([out, out]), 700)[n:]

    # Four soft pulses, at exact two-second spacing. Each decays well inside the
    # gap, so the last one is silent before the loop point.
    pulse = timpani(55.0, 1.4) * 0.30
    for beat in range(4):
        start = int(beat * 2.0 * RATE)
        end = min(n, start + len(pulse))
        out[start:end] += pulse[: end - start]

    return normalise(out, 0.5)


#: Sounds that come from a CC0 archive rather than from an oscillator. See
#: data/audio_sources.json for where each one is from and the sha256 that proves
#: it, and tools/fetch_audio.py to fetch them. Anything not listed there is still
#: synthesised below and is ours outright.
SOURCES = ROOT / "data" / "audio_sources.json"
CACHE = ROOT / "data" / "audio_cache"


def sampled(name: str) -> np.ndarray | None:
    """One sampled sting, trimmed, de-clicked and levelled, or None.

    Stereo is kept. It used to be folded to mono here, on the reasoning that a
    200 ms blip has no perceptible width through a phone speaker -- true of the
    blips, and wrong about everything else. Eleven of the twelve replacements are
    stereo recordings, and a crowd is the sound whose whole character is that it
    is around you. "A mono tone" was half the original complaint and this is the
    half of it that was literally true.
    """
    if not SOURCES.is_file():
        return None
    spec = json.loads(SOURCES.read_text("utf-8")).get("sounds", {})
    if name not in spec:
        return None
    wav = CACHE / f"{name}.wav"
    if not wav.is_file():
        print(f"  {name}: listed in audio_sources.json but not fetched; "
              f"run tools/fetch_audio.py. Falling back to synthesis.", file=sys.stderr)
        return None

    with wave.open(str(wav)) as handle:
        channels = handle.getnchannels()
        raw = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    audio = raw.astype(float).reshape(-1, channels) / 32768
    if channels == 1:
        audio = np.repeat(audio, 2, axis=1)

    trim = spec[name].get("trim")
    if trim:
        audio = audio[int(trim[0] * RATE): int(trim[1] * RATE)]
    audio = audio - audio.mean(axis=0)            # DC offset is a click waiting to happen
    audio = normalise(audio, 0.82) * 10 ** (spec[name].get("gain_db", 0.0) / 20)
    # The fade is not cosmetic. `buzzer` ends at 0.64 of full scale, and a buffer
    # that stops there is a step function into the speaker: the pop people hear
    # is not in the recording, it is at its edge.
    return fade(audio, ms=5.0)


SOUNDS = {
    "horn_01": sound_horn_01, "horn_02": sound_horn_02, "horn_03": sound_horn_03,
    "trombone": sound_trombone, "whoosh": sound_whoosh, "riser": sound_riser,
    "chime": sound_chime, "doom": sound_doom, "scratch": sound_scratch,
    "buzzer": sound_buzzer, "crowd": sound_crowd,
    "tap": sound_tap, "flip": sound_flip, "rip": sound_rip,
}

#: Which bus each sound belongs to, so the front end does not have to guess.
BUSES = {
    "horn_01": "stings", "horn_02": "stings", "horn_03": "stings",
    "trombone": "stings", "whoosh": "stings", "riser": "stings",
    "chime": "stings", "doom": "stings", "scratch": "stings",
    "buzzer": "stings", "crowd": "music",
    "tap": "ui", "flip": "ui", "rip": "ui",
}


def write_wav(path: Path, samples: np.ndarray) -> None:
    import wave

    pcm = np.clip(samples, -1, 1)
    channels = pcm.shape[1] if pcm.ndim == 2 else 1
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((pcm * 32767).astype("<i2").tobytes())


def encode(source: Path, target: Path, args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), *args, str(target)],
                   check=True, capture_output=True, timeout=180)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preview", help="write one sound as a wav and stop")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    if args.preview:
        if args.preview not in SOUNDS:
            print(f"unknown sound; try one of {', '.join(sorted(SOUNDS))}", file=sys.stderr)
            return 1
        path = OUT / f"_preview_{args.preview}.wav"
        write_wav(path, SOUNDS[args.preview]())
        print(path)
        return 0

    pieces: list[np.ndarray] = []
    sprite: dict[str, list] = {}
    cursor = 0.0
    gap = np.zeros((int(GAP * RATE), 2))

    for name in SOUNDS:
        samples = sampled(name)
        if samples is None:
            # A synthesised fallback is mono; carry it into both channels so the
            # sprite has one shape throughout.
            samples = np.repeat(SOUNDS[name]()[:, None], 2, axis=1)
        # Digital silence after the fade, longer than the safety trim below.
        #
        # `fade()` ramps the last 6 ms to zero so the buffer ends quietly, and
        # then the sprite window below ends 8 ms EARLY so a slow seek cannot run
        # into the next sound. Eight is more than six: playback therefore stopped
        # two milliseconds before the fade even began, cutting every sustained
        # sting off at full amplitude. `riser` ended at 0.23 of full scale, which
        # is a step function into the speaker -- the pop is not in the sound, it
        # is at the edge of the window. The safety trim was defeating the fade it
        # was supposed to be protecting.
        samples = np.concatenate([samples, np.zeros((int(TAIL * RATE), 2))])
        duration = len(samples) / RATE
        # Howler wants [offset_ms, duration_ms]; the duration is deliberately a
        # few milliseconds short of the real length so a slow seek cannot run
        # into the gap and pick up the head of the next sound.
        sprite[name] = [round(cursor * 1000), max(20, round(duration * 1000) - 8)]
        pieces.extend([samples, gap])
        cursor += duration + GAP

    full = normalise(np.concatenate(pieces), 0.92)
    wav = OUT / "sprite.wav"
    write_wav(wav, full)

    # The bed is its own file, not a sprite entry: Howler loops a whole file
    # cleanly and loops a sprite region with an audible gap at the seek.
    bed = sound_bed()
    # A seamless loop does not mean bed[0] == bed[-1]. Wrapping from the last
    # sample to the first is one ordinary sample step, and for a 55 Hz tone at
    # 44.1 kHz that step is naturally about 0.008 per unit amplitude -- the first
    # version of this check flagged exactly that as a fault. What matters is
    # whether the step at the join is bigger than the steps everywhere else,
    # because that is what is audible as a click.
    steps = np.abs(np.diff(bed))
    join = abs(float(bed[0]) - float(bed[-1]))
    typical = float(np.percentile(steps, 99))
    seam = join / max(typical, 1e-9)
    if seam > 3.0:
        print(f"WARNING: the loop join is {seam:.1f}x a typical sample step; "
              f"something is not snapped to the loop length", file=sys.stderr)
    bed_wav = OUT / "bed.wav"
    write_wav(bed_wav, bed)
    encode(bed_wav, OUT / "bed.mp3", ["-codec:a", "libmp3lame", "-b:a", "80k", "-ar", "44100"])
    encode(bed_wav, OUT / "bed.ogg", ["-codec:a", "libvorbis", "-qscale:a", "3", "-ar", "44100"])
    bed_wav.unlink()

    encode(wav, OUT / "sprite.mp3", ["-codec:a", "libmp3lame", "-b:a", "96k", "-ar", "44100"])
    encode(wav, OUT / "sprite.ogg", ["-codec:a", "libvorbis", "-qscale:a", "3", "-ar", "44100"])
    wav.unlink()

    (OUT / "sprite.json").write_text(
        json.dumps({"sprite": sprite, "buses": BUSES, "rate": RATE}, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"{len(SOUNDS)} sounds, {cursor:.1f}s, plus a {BED_SECONDS:.0f}s loop bed "
          f"(join step {seam:.2f}x typical)")
    for suffix in ("mp3", "ogg"):
        path = OUT / f"sprite.{suffix}"
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1000:.0f} kB")
    for name, (offset, duration) in sprite.items():
        print(f"    {name:<10} {offset:>6} ms  +{duration:>5} ms  [{BUSES[name]}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
