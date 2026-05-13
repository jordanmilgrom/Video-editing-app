"""Typer CLI entrypoint.

Wires the full pipeline: transcribe → takes → b-roll → match → FCPXML.
Each stage is in its own module and writes to `.roughcut-cache/` so
reruns are cheap.
"""

from __future__ import annotations

from pathlib import Path

import typer

from roughcut import broll, fcpxml, match, takes, transcribe
from roughcut.models import IngestSpec, Sequence

app = typer.Typer(add_completion=False, help="AI rough-cut tool.")


@app.command()
def main(
    interview: Path = typer.Option(..., "--interview", help="Interview footage folder."),
    broll_dir: Path = typer.Option(..., "--broll", help="B-roll footage folder."),
    output: Path = typer.Option(..., "--output", help="Destination FCPXML path."),
    script: Path | None = typer.Option(None, "--script", help="Optional script .txt."),
    cache_dir: Path = typer.Option(
        Path(".roughcut-cache"), "--cache-dir", help="Cache directory."
    ),
    model: str = typer.Option(
        transcribe.DEFAULT_WHISPER_MODEL, "--model",
        help="mlx-whisper HF repo. Default: whisper-large-v3-mlx.",
    ),
    fps: float = typer.Option(23.976, "--fps", help="Output sequence frame rate."),
) -> None:
    spec = IngestSpec(
        interview_dir=interview,
        broll_dir=broll_dir,
        output_path=output,
        script_path=script,
        cache_dir=cache_dir,
        mode="script" if script else "auto",
    )
    script_text = script.read_text(encoding="utf-8") if script and script.exists() else None

    typer.echo(f"[1/5] Transcribing interview footage from {spec.interview_dir} ...")
    transcripts = transcribe.transcribe_folder(spec.interview_dir, spec.cache_dir, model=model)
    typer.echo(f"      {len(transcripts)} file(s) transcribed.")

    typer.echo(f"[2/5] Picking best takes ({spec.mode} mode) ...")
    all_takes = []
    for t in transcripts:
        all_takes.extend(takes.detect_takes(t, script_text=script_text))
    chosen = sum(1 for tk in all_takes if tk.chosen)
    typer.echo(f"      {chosen} chosen take(s), {len(all_takes) - chosen} alt(s).")

    typer.echo(f"[3/5] Analyzing b-roll from {spec.broll_dir} ...")
    clips = broll.analyze_folder(spec.broll_dir, spec.cache_dir)
    typer.echo(f"      {len(clips)} clip(s) catalogued.")

    typer.echo("[4/5] Matching b-roll to A-roll ...")
    matches = match.match(all_takes, clips)
    typer.echo(f"      {len(matches)} b-roll insert(s).")

    typer.echo(f"[5/5] Writing FCPXML to {spec.output_path} ...")
    sequence = Sequence(
        name=spec.output_path.stem or "roughcut",
        frame_rate=fps,
        takes=all_takes,
        broll=matches,
        clips=clips,
    )
    fcpxml.write_fcpxml(sequence, spec.output_path)
    typer.echo("      Done.")


if __name__ == "__main__":
    app()
