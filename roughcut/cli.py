"""Typer CLI entrypoint.

Wires every pipeline stage together. Stages are implemented in their own
modules; this file does argument parsing and orchestration only.
"""

from __future__ import annotations

from pathlib import Path

import typer

from roughcut.models import IngestSpec

app = typer.Typer(add_completion=False, help="AI rough-cut tool.")


@app.command()
def main(
    interview: Path = typer.Option(..., "--interview", help="Interview footage folder."),
    broll: Path = typer.Option(..., "--broll", help="B-roll footage folder."),
    output: Path = typer.Option(..., "--output", help="Destination FCPXML path."),
    script: Path | None = typer.Option(None, "--script", help="Optional script .txt."),
    cache_dir: Path = typer.Option(
        Path(".roughcut-cache"), "--cache-dir", help="Cache directory."
    ),
) -> None:
    spec = IngestSpec(
        interview_dir=interview,
        broll_dir=broll,
        output_path=output,
        script_path=script,
        cache_dir=cache_dir,
        mode="script" if script else "auto",
    )
    typer.echo(f"[roughcut] spec parsed: {spec.model_dump_json(indent=2)}")
    # Pipeline stages will be wired here as each module lands.
    raise typer.Exit(code=0)


if __name__ == "__main__":
    app()
