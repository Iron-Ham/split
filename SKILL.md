---
name: split
description: Split a large branch into N stacked logical branches with hunk-level granularity
argument-hint: "[N] [--base <branch>]"
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion
---

# Split Branch into Stacked Logical Branches

You are executing the `/split` skill. Your job is to take the current branch's diff against a base branch, intelligently group changes into N logical units (with hunk-level granularity), and create stacked branches — each building on the previous — ready for separate PRs.

**CRITICAL: Follow these phases exactly. Do NOT skip Phase 4 (user approval).**

---

## Phase 0: Parse Arguments

Extract from `$ARGUMENTS`:
- **N** (optional integer): Number of groups to split into. If omitted, you decide based on analysis.
- **--base <branch>** (optional): Base branch to diff against. Default: auto-detect (`main` or `master`).

Examples:
- `/split 3` → N=3, base=auto
- `/split --base develop` → N=auto, base=develop
- `/split 4 --base main` → N=4, base=main

---

## Phase 1: Pre-flight Checks

Run these checks. **Abort with a clear message if any fail.**

```bash
# 1. Verify git repo
git rev-parse --git-dir

# 2. Check for clean worktree (no uncommitted changes)
git diff --quiet && git diff --cached --quiet

# 3. Get current branch name
CURRENT_BRANCH=$(git symbolic-ref --short HEAD)

# 4. Detect base branch (if not provided)
# Try: main, master, develop — whichever exists on the remote
BASE_BRANCH=${provided_base:-$(git branch -r | grep -oE 'origin/(main|master)' | head -1 | sed 's|origin/||')}

# 5. Find merge base
MERGE_BASE=$(git merge-base HEAD origin/$BASE_BRANCH)
```

If the worktree is dirty, tell the user: "Working tree has uncommitted changes. Please commit or stash them first."

Print a summary:
```
Branch:     $CURRENT_BRANCH
Base:       $BASE_BRANCH
Merge base: ${MERGE_BASE:0:8}
```

---

## Phase 2: Analyze the Diff

Run these commands to gather the full picture:

```bash
# File-level summary
git diff --name-status $MERGE_BASE..HEAD

# Stats
git diff --stat $MERGE_BASE..HEAD

# Full diff (for your analysis)
git diff $MERGE_BASE..HEAD
```

Also run the analyze helper to get structured hunk data:

```bash
git diff $MERGE_BASE..HEAD | python3 ~/.claude/skills/split/scripts/split_diff.py analyze
```

Display a summary table for the user:

```
## Changes to split

| File | Status | Hunks | +/- |
|------|--------|-------|-----|
| src/models/User.ts | Added | 1 | +45 |
| src/api/routes.ts | Modified | 3 | +22/-5 |
| ... | ... | ... | ... |

Total: X files changed, Y insertions, Z deletions
```

---

## Phase 3: Propose a Split Plan

Analyze ALL the changes and group them into N logical units. Consider:

1. **Dependency order** — Models/types before logic before API routes before tests
2. **Layer separation** — Data layer, business logic, presentation, tests
3. **Feature cohesion** — Related changes stay together
4. **Reviewability** — Each group should make sense as a standalone review
5. **Hunk granularity** — If a file has changes for multiple groups, split by hunks

**Branch naming:** Derive from the current branch name.
- If branch is `Iron-Ham/big-feature`, create: `Iron-Ham/big-feature-1-models`, `Iron-Ham/big-feature-2-api`, etc.
- The suffix is `{number}-{short-name}` appended with a hyphen.

**Build the spec mentally, then present the plan:**

```
## Proposed Split Plan

### Branch 1: Iron-Ham/big-feature-1-models
> feat: add user and auth data models

| File | Change | Hunks |
|------|--------|-------|
| src/models/User.ts | A (whole file) | all |
| src/models/Auth.ts | A (whole file) | all |
| src/index.ts | M (partial) | #0, #1 |

### Branch 2: Iron-Ham/big-feature-2-api
> feat: add authentication API routes
> (includes all changes from branch 1)

| File | Change | Hunks |
|------|--------|-------|
| src/api/auth.ts | A (whole file) | all |
| src/api/routes.ts | M (all hunks) | all |
| src/index.ts | M (partial) | #2 |

...
```

Make clear that branches are **stacked** — each includes all changes from previous branches plus its own.

---

## Phase 4: User Approval

**STOP HERE. You MUST wait for user input.**

Ask the user:

> Does this split plan look good? You can:
> - **Approve** — I'll create the branches
> - **Adjust** — Tell me what to move between groups
> - **Cancel** — Abort without changes

Use AskUserQuestion with options: "Approve", "Adjust", "Cancel"

If the user adjusts, revise the plan and ask again. If the user cancels, stop immediately.

---

## Phase 5: Execute the Split

**Important rules:**
- NEVER modify the original branch
- Track all created branches for rollback messaging
- Each branch is based on the merge base, with cumulative changes applied

### Execution algorithm:

```
For each group K (1 to N):
  1. Start from merge base:
     git checkout -b $BRANCH_NAME $MERGE_BASE

  2. For each file in groups 1..K (cumulative):
     Apply changes based on type (see table below)

  3. Stage and commit:
     git add -A
     git commit -m "$COMMIT_MESSAGE"
```

### Change application methods:

For each file, determine the method based on change type and hunk selection:

| Scenario | Method |
|----------|--------|
| Added file (A), hunks = "all" | `git checkout $ORIGINAL_BRANCH -- $FILE` |
| Modified file (M), hunks = "all" | `git checkout $ORIGINAL_BRANCH -- $FILE` |
| Modified file (M), partial hunks | Use reconstruct (see below) |
| Deleted file (D) | `git rm $FILE` |
| Renamed file (R) | `git checkout $ORIGINAL_BRANCH -- $NEW_PATH` then `git rm $OLD_PATH` |
| Binary file | `git checkout $ORIGINAL_BRANCH -- $FILE` |

### For partial hunk application:

First, write the JSON spec file to a temp location:

```bash
cat > /tmp/split_spec.json << 'SPEC'
{
  "version": 1,
  "original_branch": "...",
  "base_commit": "...",
  "groups": [...]
}
SPEC
```

Then for each file needing partial hunks in group K, reconstruct it:

```bash
# Get base file content
git show $MERGE_BASE:$FILE_PATH > /tmp/split_base_file

# Get the diff for this file
git diff $MERGE_BASE..$ORIGINAL_BRANCH -- $FILE_PATH > /tmp/split_file_diff

# Reconstruct with hunks from groups 1..K
python3 ~/.claude/skills/split/scripts/split_diff.py reconstruct \
  --base-file /tmp/split_base_file \
  --diff-file /tmp/split_file_diff \
  --hunks 0,1,3,5 \
  --output $FILE_PATH
```

The `--hunks` flag takes a comma-separated list of 0-based hunk indices to apply.

### After creating all branches:

Return to the original branch:
```bash
git checkout $ORIGINAL_BRANCH
```

### On failure:

If any step fails:
1. Print which branches were created
2. Print cleanup commands: `git branch -D $BRANCH1 $BRANCH2 ...`
3. Return to the original branch
4. **Never auto-delete branches**

---

## Phase 6: Summary

Print a final summary:

```
## Split Complete!

Created N stacked branches from `$ORIGINAL_BRANCH`:

| # | Branch | Commit | Files |
|---|--------|--------|-------|
| 1 | Iron-Ham/big-feature-1-models | feat: add models | 3 |
| 2 | Iron-Ham/big-feature-2-api | feat: add API | 5 |
| 3 | Iron-Ham/big-feature-3-tests | test: add tests | 4 |

### Push commands:
git push -u origin Iron-Ham/big-feature-1-models
git push -u origin Iron-Ham/big-feature-2-api
git push -u origin Iron-Ham/big-feature-3-tests

### PR creation (each PR targets the previous branch):
gh pr create --base main --head Iron-Ham/big-feature-1-models --title "Add models"
gh pr create --base Iron-Ham/big-feature-1-models --head Iron-Ham/big-feature-2-api --title "Add API"
gh pr create --base Iron-Ham/big-feature-2-api --head Iron-Ham/big-feature-3-tests --title "Add tests"
```

The first PR targets the base branch. Each subsequent PR targets the previous stacked branch.

---

## Reminders

- **NEVER modify the original branch.** All work happens on new branches created from the merge base.
- **ALWAYS wait for user approval in Phase 4.** Never skip ahead.
- **Cumulative stacking:** Branch K contains changes from groups 1 through K.
- **Hunk indices are 0-based** in the JSON spec and split_diff.py.
- If N is not specified, choose a reasonable number (typically 2-5) based on the logical structure of the changes.
