# split

A Claude Code skill that splits a large branch into N stacked logical branches with hunk-level granularity.

## What it does

Large branches are hard to review. `/split` analyzes your branch's diff against its base, groups changes into logical units (models, API, tests, etc.), and creates stacked branches — each building on the previous — ready for separate PRs.

## Install

### Claude Code Marketplace

```bash
claude plugin install Iron-Ham/split
```

### Manual Install

Clone the repo and copy the skill files into Claude Code's skills directory:

```bash
git clone https://github.com/Iron-Ham/split.git
cd split
mkdir -p ~/.claude/skills/split/scripts
cp SKILL.md ~/.claude/skills/split/
cp scripts/split_diff.py ~/.claude/skills/split/scripts/
chmod +x ~/.claude/skills/split/scripts/split_diff.py
```

This also works for other AI coding agents that support a skills/tools directory (Codex, Cursor, etc.) — just copy `SKILL.md` and `scripts/split_diff.py` to wherever your agent reads custom instructions from.

### Requirements

- Python 3.10+ (stdlib only, no dependencies)
- `gh` CLI (for automatic PR creation)

## Usage

```
/split              # Auto-detect number of groups
/split 3            # Split into 3 groups
/split --base dev   # Diff against 'dev' instead of main
/split 4 --base dev # Both
```

## How it works

1. **Analyze** — Diffs your branch against the base, summarizes files and hunks
2. **Propose** — Groups changes into N logical units by layer/feature/dependency
3. **Approve** — Shows the plan; you can approve, adjust, or cancel
4. **Execute** — Creates stacked branches, each with cumulative changes
5. **Summary** — Prints push and PR creation commands

### Stacking model

Branch 2 contains all changes from branch 1 plus its own. Branch 3 contains branches 1+2 plus its own. Each PR targets the previous branch.

```
main ─── branch-1-models ─── branch-2-api ─── branch-3-tests
```

### Hunk-level splitting

If a single file has changes belonging to different groups, the skill splits at the hunk level — only the relevant hunks are applied to each branch.

## Limitations

- Requires a clean worktree (no uncommitted changes)
- The original branch is never modified
- On failure, cleanup commands are printed but branches are not auto-deleted

## License

MIT
