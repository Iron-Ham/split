#!/usr/bin/env python3
"""Hunk-level patch helper for the /split skill.

Two commands:
  analyze     - Parse a unified diff from stdin, output structured JSON summary.
  reconstruct - Rebuild a file by applying a subset of hunks from a diff.

No third-party dependencies — stdlib only.
"""

import argparse
import json
import re
import sys
import os

# --------------------------------------------------------------------------- #
# Hunk parsing
# --------------------------------------------------------------------------- #

HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)"
)
DIFF_FILE_RE = re.compile(r"^diff --git a/(.*) b/(.*)")
STATUS_RE = re.compile(r"^(new|deleted) file mode")
BINARY_RE = re.compile(r"^Binary files")
RENAME_FROM_RE = re.compile(r"^rename from (.*)")
RENAME_TO_RE = re.compile(r"^rename to (.*)")


class Hunk:
    """A single diff hunk."""

    def __init__(
        self,
        index: int,
        old_start: int,
        old_count: int,
        new_start: int,
        new_count: int,
        header_context: str,
        lines: list[str],
    ):
        self.index = index
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.header_context = header_context.strip()
        self.lines = lines  # raw diff lines (starting with +, -, or space)

    @property
    def additions(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("+"))

    @property
    def deletions(self) -> int:
        return sum(1 for ln in self.lines if ln.startswith("-"))

    def content_preview(self, max_lines: int = 3) -> list[str]:
        added = [ln[1:] for ln in self.lines if ln.startswith("+")]
        return [ln.strip() for ln in added[:max_lines]]


class FileDiff:
    """Parsed diff for a single file."""

    def __init__(self, old_path: str, new_path: str):
        self.old_path = old_path
        self.new_path = new_path
        self.hunks: list[Hunk] = []
        self.change_type = "M"  # default; overridden by parsing
        self.is_binary = False
        self.old_mode: str | None = None
        self.new_mode: str | None = None

    @property
    def path(self) -> str:
        return self.new_path if self.new_path else self.old_path

    @property
    def additions(self) -> int:
        return sum(h.additions for h in self.hunks)

    @property
    def deletions(self) -> int:
        return sum(h.deletions for h in self.hunks)


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff into a list of FileDiff objects."""
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None
    hunk_index = 0

    for line in diff_text.splitlines():
        # New file header
        m = DIFF_FILE_RE.match(line)
        if m:
            # Finalize previous hunk
            if current_hunk and current_file:
                current_file.hunks.append(current_hunk)
                current_hunk = None

            old_path, new_path = m.group(1), m.group(2)
            current_file = FileDiff(old_path, new_path)
            files.append(current_file)
            hunk_index = 0
            continue

        if current_file is None:
            continue

        # Detect new/deleted file
        sm = STATUS_RE.match(line)
        if sm:
            if sm.group(1) == "new":
                current_file.change_type = "A"
            elif sm.group(1) == "deleted":
                current_file.change_type = "D"
            continue

        # Detect binary
        if BINARY_RE.match(line):
            current_file.is_binary = True
            continue

        # Detect rename
        rm = RENAME_FROM_RE.match(line)
        if rm:
            current_file.change_type = "R"
            current_file.old_path = rm.group(1)
            continue
        rm = RENAME_TO_RE.match(line)
        if rm:
            current_file.new_path = rm.group(1)
            continue

        # Hunk header
        hm = HUNK_HEADER_RE.match(line)
        if hm:
            # Finalize previous hunk
            if current_hunk:
                current_file.hunks.append(current_hunk)

            old_start = int(hm.group(1))
            old_count = int(hm.group(2)) if hm.group(2) else 1
            new_start = int(hm.group(3))
            new_count = int(hm.group(4)) if hm.group(4) else 1
            header_ctx = hm.group(5)

            current_hunk = Hunk(
                index=hunk_index,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header_context=header_ctx,
                lines=[],
            )
            hunk_index += 1
            continue

        # Hunk content lines
        if current_hunk is not None and (
            line.startswith("+")
            or line.startswith("-")
            or line.startswith(" ")
            or line == "\\ No newline at end of file"
        ):
            current_hunk.lines.append(line)

    # Finalize last hunk
    if current_hunk and current_file:
        current_file.hunks.append(current_hunk)

    return files


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_analyze(args: argparse.Namespace) -> None:
    """Read a unified diff from stdin and output structured JSON."""
    diff_text = sys.stdin.read()
    files = parse_diff(diff_text)

    output = {
        "file_count": len(files),
        "files": [],
    }

    for f in files:
        file_info = {
            "path": f.path,
            "old_path": f.old_path if f.old_path != f.new_path else None,
            "change_type": f.change_type,
            "is_binary": f.is_binary,
            "hunk_count": len(f.hunks),
            "additions": f.additions,
            "deletions": f.deletions,
            "hunks": [],
        }

        for h in f.hunks:
            hunk_info = {
                "index": h.index,
                "old_start": h.old_start,
                "old_count": h.old_count,
                "new_start": h.new_start,
                "new_count": h.new_count,
                "additions": h.additions,
                "deletions": h.deletions,
                "context": h.header_context,
                "preview": h.content_preview(3),
            }
            file_info["hunks"].append(hunk_info)

        output["files"].append(file_info)

    json.dump(output, sys.stdout, indent=2)
    sys.stdout.write("\n")


def cmd_reconstruct(args: argparse.Namespace) -> None:
    """Rebuild a file by applying a subset of hunks from a diff.

    Reads the base file content and the full diff for that file,
    then applies only the specified hunks (by 0-based index).
    Writes the reconstructed file to --output (or stdout).
    """
    # Read base file
    try:
        with open(args.base_file, "r", encoding="utf-8") as f:
            base_lines = f.readlines()
    except OSError as e:
        print(f"Error: cannot read base file '{args.base_file}': {e}", file=sys.stderr)
        sys.exit(1)

    # Read diff
    try:
        with open(args.diff_file, "r", encoding="utf-8") as f:
            diff_text = f.read()
    except OSError as e:
        print(f"Error: cannot read diff file '{args.diff_file}': {e}", file=sys.stderr)
        sys.exit(1)

    # Parse hunks from the diff
    files = parse_diff(diff_text)
    if not files:
        print("Error: no file diffs found in diff file", file=sys.stderr)
        sys.exit(1)

    # Warn if diff contains multiple files (only the first is used)
    if len(files) > 1:
        print(
            f"Warning: diff contains {len(files)} files, using only the first: {files[0].path}",
            file=sys.stderr,
        )
    file_diff = files[0]

    # Parse hunk indices to apply
    if args.hunks:
        try:
            selected_indices = set(
                int(x.strip()) for x in args.hunks.split(",") if x.strip()
            )
        except ValueError:
            print(
                f"Error: invalid hunk indices '{args.hunks}'. "
                "Expected comma-separated integers (0-based).",
                file=sys.stderr,
            )
            sys.exit(1)

        # Validate hunk indices against available hunks
        available = set(h.index for h in file_diff.hunks)
        invalid = selected_indices - available
        if invalid:
            print(
                f"Error: hunk indices {sorted(invalid)} out of range. "
                f"Available: 0-{len(file_diff.hunks) - 1} "
                f"({len(file_diff.hunks)} hunks total).",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        selected_indices = set(range(len(file_diff.hunks)))

    # Filter to selected hunks
    selected_hunks = [h for h in file_diff.hunks if h.index in selected_indices]

    if not selected_hunks:
        # No hunks selected — output the base file unchanged
        result = "".join(base_lines)
        _write_output(result, args.output)
        return

    # Apply hunks to base content
    result_lines = apply_hunks(base_lines, selected_hunks)
    result = "".join(result_lines)

    _write_output(result, args.output)


def apply_hunks(base_lines: list[str], hunks: list[Hunk]) -> list[str]:
    """Apply a list of hunks to base file lines.

    Hunks are sorted internally by old_start position before application.
    Uses offset tracking to handle line number shifts from previous hunks.
    """
    result: list[str] = []
    base_idx = 0  # current position in base_lines (0-based)
    base_len = len(base_lines)

    # Sort hunks by old_start to ensure correct ordering
    sorted_hunks = sorted(hunks, key=lambda h: h.old_start)

    # Validate no overlapping hunks
    for i in range(len(sorted_hunks) - 1):
        curr = sorted_hunks[i]
        nxt = sorted_hunks[i + 1]
        curr_end = curr.old_start + curr.old_count  # 1-based exclusive end
        if curr_end > nxt.old_start:
            print(
                f"Warning: hunks {curr.index} and {nxt.index} overlap "
                f"(lines {curr.old_start}-{curr_end - 1} and {nxt.old_start}-"
                f"{nxt.old_start + nxt.old_count - 1})",
                file=sys.stderr,
            )

    for hunk in sorted_hunks:
        hunk_start = hunk.old_start - 1  # convert to 0-based

        # Copy unchanged lines before this hunk
        while base_idx < hunk_start and base_idx < base_len:
            result.append(base_lines[base_idx])
            base_idx += 1

        # Apply hunk lines
        no_newline_pending = False
        for line in hunk.lines:
            if line == "\\ No newline at end of file":
                # Remove trailing newline from the last added line
                if result and result[-1].endswith("\n"):
                    result[-1] = result[-1][:-1]
                no_newline_pending = True
                continue

            if line.startswith("-"):
                # Deleted line — skip it in base
                if base_idx >= base_len:
                    print(
                        f"Warning: hunk {hunk.index} references line {base_idx + 1} "
                        f"but base file has only {base_len} lines",
                        file=sys.stderr,
                    )
                else:
                    base_idx += 1
            elif line.startswith("+"):
                # Added line
                result.append(line[1:] + "\n")
            elif line.startswith(" "):
                # Context line
                if base_idx >= base_len:
                    print(
                        f"Warning: hunk {hunk.index} references line {base_idx + 1} "
                        f"but base file has only {base_len} lines",
                        file=sys.stderr,
                    )
                    result.append(line[1:] + "\n")
                else:
                    result.append(line[1:] + "\n")
                    base_idx += 1
            else:
                # Unexpected line format
                print(
                    f"Warning: unexpected diff line format in hunk {hunk.index}: "
                    f"{line[:40]!r}",
                    file=sys.stderr,
                )
                result.append(line + "\n")
                base_idx += 1

        if no_newline_pending and result and result[-1].endswith("\n"):
            result[-1] = result[-1][:-1]

    # Copy remaining lines after last hunk
    while base_idx < base_len:
        result.append(base_lines[base_idx])
        base_idx += 1

    return result


def _write_output(content: str, output_path: str | None) -> None:
    """Write content to a file or stdout."""
    if output_path:
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            print(f"Error: cannot write to '{output_path}': {e}", file=sys.stderr)
            sys.exit(1)
    else:
        sys.stdout.write(content)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hunk-level patch helper for the /split skill."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # analyze
    subparsers.add_parser(
        "analyze",
        help="Parse a unified diff from stdin and output structured JSON.",
    )

    # reconstruct
    recon = subparsers.add_parser(
        "reconstruct",
        help="Rebuild a file by applying a subset of hunks from a diff.",
    )
    recon.add_argument(
        "--base-file",
        required=True,
        help="Path to the base file content (from git show).",
    )
    recon.add_argument(
        "--diff-file",
        required=True,
        help="Path to the diff file for a single file.",
    )
    recon.add_argument(
        "--hunks",
        default=None,
        help="Comma-separated 0-based hunk indices to apply. Omit for all.",
    )
    recon.add_argument(
        "--output",
        default=None,
        help="Output file path. Defaults to stdout.",
    )

    args = parser.parse_args()

    if args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "reconstruct":
        cmd_reconstruct(args)


if __name__ == "__main__":
    main()
