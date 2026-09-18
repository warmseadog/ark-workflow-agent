#!/usr/bin/env python3
"""Batch local face-mosaic processing with portable paths and timing summary."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--robust", action="store_true")
    parser.add_argument("--all-faces", action="store_true")
    parser.add_argument("--no-hair", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--encoder", default="auto")
    args = parser.parse_args()

    if not args.input_dir.is_dir():
        raise RuntimeError(f"Input directory not found: {args.input_dir}")
    if args.workers < 1:
        raise RuntimeError("--workers must be at least 1")

    iterator = args.input_dir.rglob("*") if args.recursive else args.input_dir.glob("*")
    inputs = sorted(
        path for path in iterator
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not inputs:
        raise RuntimeError(f"No supported videos found in: {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    processor = Path(__file__).with_name(
        "process_all_faces_mosaic.py" if args.all_faces
        else "process_primary_face_mosaic.py"
    )
    jobs = []
    skipped = []
    for source in inputs:
        relative = source.relative_to(args.input_dir)
        target_dir = args.output_dir / relative.parent if args.recursive else args.output_dir
        target = target_dir / f"{source.stem}_人脸打码.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not args.overwrite:
            skipped.append(str(target))
        else:
            jobs.append((source, target))

    started = time.perf_counter()

    def run(job):
        source, target = job
        command = [
            sys.executable, str(processor), str(source), str(target),
            "--encoder", args.encoder,
        ]
        if args.ffmpeg:
            command.extend(["--ffmpeg", str(args.ffmpeg)])
        if args.robust and not args.all_faces:
            command.append("--robust")
        if args.no_hair:
            command.append("--no-hair")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
        stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
        return {
            "input": str(source), "output": str(target),
            "status": "ok" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "last_output": stdout_lines[-1:] or stderr_lines[-1:],
        }

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(run, jobs):
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

    elapsed = time.perf_counter() - started
    failed = [result for result in results if result["status"] == "failed"]
    summary = {
        "input_dir": str(args.input_dir), "output_dir": str(args.output_dir),
        "videos_found": len(inputs), "processed": len(results) - len(failed),
        "failed": len(failed), "skipped": len(skipped), "workers": args.workers,
        "mode": (
            "all_faces" if args.all_faces
            else ("robust" if args.robust else "fast")
        ),
        "elapsed_seconds": round(elapsed, 3), "processing": "local",
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
