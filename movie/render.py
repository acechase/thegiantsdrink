"""Animate journal paintings, time narration and captions, and assemble the film with FFmpeg."""

import math
import os
import re
import shutil
import subprocess
import wave
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from movie.screenplay import MovieError, scene_art

WIDTH, HEIGHT = 1280, 720
SIZE = (WIDTH, HEIGHT)
FPS = 24
SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = SAMPLE_RATE // FPS
FRAME_BYTES = SAMPLES_PER_FRAME * 4  # Stereo signed 16-bit PCM.
HEAD_FRAMES = 6
INTRO_FRAMES = 54
DISSOLVE = 7
INK = (13, 19, 27)
PAPER = (250, 237, 212)
GOLD = (233, 184, 112)
FONT_PATHS = (
    "/System/Library/Fonts/Supplemental/{name}",
    "/usr/share/fonts/truetype/dejavu/{name}",
    "C:/Windows/Fonts/{name}",
)


def _ffmpeg(args):
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)]


def _run(args, **kwargs):
    process = subprocess.run(_ffmpeg(args), capture_output=True, **kwargs)
    if process.returncode:
        reason = process.stderr.decode(errors="replace").strip().splitlines()
        raise MovieError(f"FFmpeg failed. {reason[-1] if reason else 'Check your FFmpeg installation.'}")
    return process.stdout


@lru_cache(maxsize=20)
def _font(size, serif=False):
    names = ("Georgia.ttf", "DejaVuSerif.ttf", "georgia.ttf") if serif else (
        "Arial.ttf", "DejaVuSans.ttf", "arial.ttf"
    )
    for path, name in zip(FONT_PATHS, names):
        try:
            return ImageFont.truetype(path.format(name=name), size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap(text, font, width):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return "\n".join([*lines, current])


def _title(frame, title, *, opening):
    image = Image.blend(frame, Image.new("RGB", SIZE, INK), .59 if opening else .72)
    draw = ImageDraw.Draw(image)
    if opening:
        draw.text((WIDTH / 2, 238), "THE GIANT’S DRINK", font=_font(22), fill=GOLD, anchor="mm")
    draw.multiline_text((WIDTH / 2, HEIGHT / 2), _wrap(title, _font(70, serif=True), 1070),
                        font=_font(70, serif=True), fill=PAPER, anchor="mm", align="center", spacing=9)
    if opening:
        draw.line((WIDTH / 2 - 58, 476, WIDTH / 2 + 58, 476), fill=GOLD, width=1)
    return image


def _caption(text, speaker):
    label = f"“{text}”" if speaker == "giant" else text
    font = _font(32)
    while font.getlength(max(label.split(), key=len)) > 990 and font.size > 14:
        font = _font(font.size - 2)
    label = _wrap(label, font, 990)
    measuring = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = measuring.multiline_textbbox((0, 0), label, font=font, spacing=7)
    width, height = math.ceil(right - left) + 50, math.ceil(bottom - top) + 26
    plate = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(plate)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), 10, fill=(9, 15, 24, 221), outline=(237, 196, 141, 85))
    draw.multiline_text((width / 2, 13 - top), label, font=font, anchor="ma", align="center", fill=PAPER, spacing=7)
    return plate


def _caption_cards(scene, start, length):
    if not scene.text:
        return []
    # Divide long passages into readable cards. Within a take, timing is proportional to the text.
    phrases = re.split(r"(?<=[.!?]) +", scene.text)
    pieces = []
    for phrase in phrases:
        words = phrase.split()
        group_count = math.ceil(len(words) / 14)
        step = math.ceil(len(words) / group_count)
        pieces.extend(" ".join(words[i:i + step]) for i in range(0, len(words), step))
    total = sum(len(piece) for piece in pieces)
    end, cards = start, []
    for piece in pieces:
        end += length * len(piece) / total
        cards.append((end, _caption(piece, scene.speaker)))
    return cards


def _painting(path, kind):
    try:
        with Image.open(path) as image:
            art = ImageOps.exif_transpose(image).convert("RGB")
    except (OSError, Image.DecompressionBombError):
        return None
    fitted = ImageOps.fit(art, SIZE, Image.Resampling.LANCZOS)
    if kind in ("portrait", "cutscene"):
        # Keep the character and equipment within the action-safe area during the push.
        soft = fitted.filter(ImageFilter.GaussianBlur(34))
        fitted = Image.blend(soft, Image.new("RGB", SIZE, INK), .62)
        foreground = ImageOps.contain(art, (WIDTH - 88, HEIGHT - 68), Image.Resampling.LANCZOS)
        fitted.paste(foreground, ((WIDTH - foreground.width) // 2, (HEIGHT - foreground.height) // 2))
    return fitted


def _move(image, frame, count, motion):
    if motion == "hold":
        return image
    t = frame / max(1, count - 1)
    eased = t * t * (3 - 2 * t)
    zoom = 1 + .085 * (eased if motion == "push" else 1 - eased)
    width, height = WIDTH / zoom, HEIGHT / zoom
    return image.transform(SIZE, Image.Transform.EXTENT,
                           ((WIDTH - width) / 2, (HEIGHT - height) / 2, (WIDTH + width) / 2, (HEIGHT + height) / 2),
                           Image.Resampling.BILINEAR)


def _audio(scene, voices):
    if scene.text and voices:
        path = voices.take(scene)
        pcm = _run(["-i", path, "-af", "highpass=f=75,loudnorm=I=-20:LRA=11:TP=-3",
                    "-ar", SAMPLE_RATE, "-ac", 2, "-f", "s16le", "pipe:1"])
        spoken = math.ceil(len(pcm) / FRAME_BYTES)
    else:
        pcm = b""
        spoken = math.ceil(len(scene.text.split()) / 2.35 * FPS)
    frames = max(math.ceil(scene.seconds * FPS), HEAD_FRAMES + spoken + 12)
    return frames, spoken, pcm


def _frames(work, screenplay, timeline, turns, art_dir, report):
    dark = Image.new("RGB", SIZE, INK)
    last = None
    clock = INTRO_FRAMES
    metadata = [";FFMETADATA1", f"title={_metadata(screenplay.title)}",
                "comment=Animated playthrough. Spoken voices are AI-generated when present."]
    with wave.open(str(work / "sound.wav"), "wb") as wav:
        wav.setparams((2, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        wav.writeframesraw(bytes(FRAME_BYTES * INTRO_FRAMES))
        for number, (scene, audio) in enumerate(zip(screenplay.scenes, timeline), 1):
            count, spoken, pcm = audio
            wav.writeframesraw(bytes(FRAME_BYTES * HEAD_FRAMES))
            wav.writeframesraw(pcm)
            wav.writeframesraw(bytes(FRAME_BYTES * (count - HEAD_FRAMES) - len(pcm)))
            pictures = [image for path, kind in scene_art(scene, turns, art_dir)
                        if (image := _painting(path, kind)) is not None]
            if not pictures:
                report(f"Scene {number} has no cached painting. Using a title card.")
                pictures = [_title(dark, scene.title, opening=False)]
                motion = "hold"
            else:
                motion = scene.motion
            if number == 1:
                opening = _title(pictures[0], screenplay.title, opening=True)
                for frame in range(INTRO_FRAMES):
                    yield Image.blend(dark, opening, min(1, frame / 9))
                last = opening
            metadata.extend(["[CHAPTER]", f"TIMEBASE=1/{FPS}", f"START={0 if number == 1 else clock}",
                             f"END={clock + count}", f"title={_metadata(scene.title)}"])
            clock += count
            cards = _caption_cards(scene, HEAD_FRAMES, spoken)
            for j, picture in enumerate(pictures):
                begin, end = j * count // len(pictures), (j + 1) * count // len(pictures)
                for frame in range(begin, end):
                    base = _move(picture, frame - begin, end - begin, motion)
                    blend = (frame - begin + 1) / DISSOLVE
                    if last is not None and blend < 1:
                        base = Image.blend(last, base, blend)
                    output = base
                    if HEAD_FRAMES <= frame < HEAD_FRAMES + spoken:
                        for cutoff, card in cards:
                            if frame < cutoff:
                                output = base.copy()
                                output.paste(card, ((WIDTH - card.width) // 2, HEIGHT - card.height - 46), card)
                                break
                    if number == len(timeline) and frame >= count - 11:
                        output = Image.blend(output, dark, (frame - count + 11) / 10)
                    yield output
                last = base
    (work / "chapters.txt").write_text("\n".join(metadata) + "\n", encoding="utf-8")


def _metadata(text):
    return re.sub(r"([\\=;#])", r"\\\1", text)


def _encode_picture(work, images):
    args = ["-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", FPS,
            "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", 19, "-pix_fmt", "yuv420p",
            work / "picture.mp4"]
    with (work / "encoder.log").open("w+b") as log, subprocess.Popen(
        _ffmpeg(args), stdin=subprocess.PIPE, stderr=log
    ) as encoder:
        try:
            for image in images:
                encoder.stdin.write(image.tobytes())
            encoder.stdin.close()
        except BrokenPipeError:
            pass  # FFmpeg's diagnostic is more informative than the closed pipe.
        finally:
            images.close()
            if not encoder.stdin.closed:
                try:
                    encoder.stdin.close()
                except BrokenPipeError:
                    pass
        if encoder.wait():
            log.seek(0)
            raise MovieError(f"Could not encode the picture. {log.read().decode(errors='replace').strip()}")


def render_movie(screenplay, turns, art_dir, output, *, voices=None, music=None, report=print):
    """Render an MP4 with animated paintings, on-screen captions, music and chapter markers."""
    if not shutil.which("ffmpeg"):
        raise MovieError("Install FFmpeg with libx264 and AAC support to render a movie.")
    timeline = [_audio(scene, voices) for scene in screenplay.scenes]
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".movie-", dir=output.parent) as temp:
        work = Path(temp)
        report("Rendering the movie locally.")
        _encode_picture(work, _frames(work, screenplay, timeline, turns, art_dir, report))
        inputs = ["-i", work / "picture.mp4", "-i", work / "sound.wav"]
        audio = ["-map", "1:a:0"]
        if music is not None:
            inputs.extend(["-stream_loop", -1, "-i", music])
            duration = (INTRO_FRAMES + sum(count for count, _, _ in timeline)) / FPS
            audio = ["-filter_complex",
                     f"[2:a]volume=0.15,afade=d=0.28,afade=t=out:st={duration - .55:.6f}:d=0.55[bed];"
                     "[1:a][bed]amix=duration=first:normalize=0,alimiter=limit=0.82:level=false:latency=true[audio]",
                     "-map", "[audio]"]
        meta = 3 if music is not None else 2
        target = work / "movie.mp4"
        _run([*inputs, "-f", "ffmetadata", "-i", work / "chapters.txt", "-map", "0:v:0", *audio,
              "-map_metadata", meta, "-map_chapters", meta, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
              "-shortest", "-movflags", "+faststart", target])
        os.replace(target, output)
