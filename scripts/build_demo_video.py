from __future__ import annotations

import html
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets" / "demo"
OUTPUT = ASSET_DIR / "ic-copilot-aapl-demo.mp4"
SUBTITLES = ASSET_DIR / "ic-copilot-aapl-demo.srt"
HUMAN_NARRATION = [
    ASSET_DIR / "0-12.m4a",
    ASSET_DIR / "12-24.m4a",
    ASSET_DIR / "24-33.m4a",
    ASSET_DIR / "33-46.m4a",
    ASSET_DIR / "46-55.m4a",
    ASSET_DIR / "55-107.m4a",
    ASSET_DIR / "107-118.m4a",
    ASSET_DIR / "118-131.m4a",
]

VIDEO_DEPENDENCIES = ROOT / "data" / ".video_deps"
if VIDEO_DEPENDENCIES.exists():
    sys.path.insert(0, str(VIDEO_DEPENDENCIES))

import imageio_ffmpeg  # noqa: E402


SCENES = [
    (
        "01-intro.png",
        "Evidence first. No invented thesis.",
        "Most research tools give you a confident answer. IC Copilot begins somewhere else: with evidence. And when the evidence is weak, it can say so.",
    ),
    (
        "03-source-claim.png",
        "1. Start with the exact reported change",
        "For Apple, the starting point is simple. Revenue and gross profit rose together, while gross margin improved. That is the change we need to explain.",
    ),
    (
        "04-evidence-drawer.png",
        "2. Inspect the source, period, and citation",
        "Open the claim, and you can see the issuer document, reporting period, citation, parser status, and the underlying numbers.",
    ),
    (
        "06-causal-graph-detail.png",
        "3. Score every link in the causal thesis",
        "From there, the app tests the chain one link at a time: source event, business driver, operating K P I, earnings or free cash flow, valuation, and catalyst.",
    ),
    (
        "09-peer-checks.png",
        "4. Test the driver against peer operating metrics",
        "It then asks whether peers show the same operating pattern. This is a metric comparison, not just a stock-price comparison.",
    ),
    (
        "10-reverse-dcf.png",
        "5. Reverse-engineer what the market price assumes",
        "Reverse D C F turns the question around: what must today's price already assume? Every input, formula, confidence level, and limitation stays visible.",
    ),
    (
        "14-bull-bear-judge.png",
        "6. Separate bull, bear, accepted, and unproven",
        "Finally, the bull, bear, and judge views separate what supports the thesis, what challenges it, what the evidence proves, and what is still open.",
    ),
    (
        "01-intro.png",
        "IC Copilot | Open-source, auditable IC research",
        "The result is not a polished guess. It is an investment committee decision you can inspect, challenge, and reproduce. IC Copilot is open source on GitHub.",
    ),
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _render_scene(source: Path, caption: str, destination: Path) -> None:
    image = Image.open(source).convert("RGB").resize((1920, 1080), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, 1920, 108), fill=(8, 13, 22, 240))
    draw.rectangle((0, 103, 1920, 108), fill=(37, 170, 160, 255))
    draw.text((56, 27), caption, font=_font(45, bold=True), fill=(248, 250, 252, 255))

    footer = "Frozen AAPL demo | Illustrative research workflow | Not investment advice"
    footer_font = _font(24)
    footer_box = draw.textbbox((0, 0), footer, font=footer_font)
    footer_width = footer_box[2] - footer_box[0]
    draw.rounded_rectangle(
        (1920 - footer_width - 64, 1020, 1896, 1065),
        radius=9,
        fill=(8, 13, 22, 230),
    )
    draw.text((1920 - footer_width - 48, 1027), footer, font=footer_font, fill=(203, 213, 225, 255))
    Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB").save(destination, quality=95)


def _synthesize(text: str, destination: Path) -> None:
    ssml_text = html.escape(text).replace(". ", '.<break time="260ms"/> ')
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        '<prosody rate="-4%" pitch="-1st">'
        f"{ssml_text}"
        "</prosody></speak>"
    )
    escaped_text = ssml.replace("'", "''")
    escaped_path = str(destination).replace("'", "''")
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$voice.SelectVoice('Microsoft David Desktop'); "
        "$voice.Volume = 100; "
        f"$voice.SetOutputToWaveFile('{escaped_path}'); "
        f"$voice.SpeakSsml('{escaped_text}'); "
        "$voice.Dispose();"
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
    )
    if not destination.exists() or destination.stat().st_size < 128:
        raise RuntimeError(f"Speech synthesis produced no usable audio: {destination.name}")


def _prepare_human_narration(source: Path, destination: Path, ffmpeg: str) -> None:
    # Remove recorder lead-in/tail noise, then apply restrained cleanup without flattening the voice.
    filters = (
        "silenceremove=start_periods=1:start_duration=0.15:start_threshold=-46dB:"
        "start_silence=0.10:detection=rms:window=0.03,"
        "areverse,"
        "silenceremove=start_periods=1:start_duration=0.20:start_threshold=-48dB:"
        "start_silence=0.32:detection=rms:window=0.03,"
        "afade=t=in:st=0:d=0.015,"
        "areverse,"
        "afade=t=in:st=0:d=0.015,"
        "highpass=f=72,"
        "lowpass=f=13800,"
        "equalizer=f=170:t=q:w=1.0:g=1.1,"
        "equalizer=f=320:t=q:w=1.2:g=-0.6,"
        "equalizer=f=3300:t=q:w=1.2:g=1.0,"
        "acompressor=threshold=0.125:ratio=2.0:attack=20:release=160:makeup=1.30,"
        "loudnorm=I=-16:TP=-1.5:LRA=8,"
        "alimiter=limit=0.94:attack=5:release=50,"
        "aresample=48000"
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-af",
            filters,
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if not destination.exists() or destination.stat().st_size < 128:
        raise RuntimeError(f"Human narration conversion produced no usable audio: {source.name}")


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def _combine_audio(paths: list[Path], destination: Path, gap_seconds: float = 0.06) -> list[float]:
    durations = [_wav_duration(path) + gap_seconds for path in paths]
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, path in enumerate(paths):
        inputs.extend(["-i", str(path)])
        label = f"a{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:a]aresample=48000,apad=pad_dur={gap_seconds:.3f},"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
    filters.append(f"{''.join(labels)}concat=n={len(paths)}:v=0:a=1[out]")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return durations


def _quote_concat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _srt_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _write_subtitles(durations: list[float]) -> None:
    start = 0.0
    cues: list[str] = []
    for index, ((_, _, narration), duration) in enumerate(zip(SCENES, durations), start=1):
        end = start + duration
        cues.extend(
            [
                str(index),
                f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}",
                narration,
                "",
            ]
        )
        start = end
    SUBTITLES.write_text("\n".join(cues), encoding="utf-8")


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="ic-copilot-demo-"))
    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        use_human_narration = all(path.exists() for path in HUMAN_NARRATION)
        frames: list[Path] = []
        audio_segments: list[Path] = []
        for index, (source_name, caption, narration) in enumerate(SCENES, start=1):
            frame = work / f"frame-{index:02d}.png"
            audio = work / f"audio-{index:02d}.wav"
            _render_scene(ASSET_DIR / source_name, caption, frame)
            if use_human_narration:
                _prepare_human_narration(HUMAN_NARRATION[index - 1], audio, ffmpeg)
            else:
                _synthesize(narration, audio)
            frames.append(frame)
            audio_segments.append(audio)

        narration_track = work / "narration.wav"
        durations = _combine_audio(audio_segments, narration_track)
        _write_subtitles(durations)
        concat_file = work / "frames.txt"
        lines: list[str] = []
        for frame, duration in zip(frames, durations):
            lines.append(f"file '{_quote_concat_path(frame)}'")
            lines.append(f"duration {duration:.3f}")
        lines.append(f"file '{_quote_concat_path(frames[-1])}'")
        concat_file.write_text("\n".join(lines), encoding="utf-8")

        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-i",
                str(narration_track),
                "-vf",
                "fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(OUTPUT),
            ],
            check=True,
        )
        print(f"Created {OUTPUT}")
        print(f"Created {SUBTITLES}")
        print(f"Duration: {sum(durations):.1f} seconds")
        print(f"Narration: {'human recordings' if use_human_narration else 'Microsoft David fallback'}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
