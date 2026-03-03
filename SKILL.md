---
name: split
description: Split a large branch into N stacked logical branches with hunk-level granularity
argument-hint: "[N] [--base <branch>] [--no-draft]"
allowed-tools: Bash, Read, AskUserQuestion
---

# Split Branch into Stacked Logical Branches

You are executing the `/split` skill. Your job is to take the current branch's diff against a base branch, intelligently group changes into N logical units (with hunk-level granularity), and create stacked branches — each building on the previous — ready for separate PRs.

**CRITICAL: Follow these phases exactly. Do NOT skip Phase 4 (user approval).**

---

## Phase 0: Parse Arguments

The skill runner populates `$ARGUMENTS` with everything after `/split`.

Extract from `$ARGUMENTS`:
- **N** (optional integer, >= 2): Number of groups to split into. If omitted, you decide based on analysis. If N < 2, abort — splitting into fewer than 2 groups is a no-op.
- **--base <branch>** (optional): Base branch to diff against. Default: auto-detect (`main` or `master`).
- **--no-draft** (optional flag): If present, create PRs as ready for review. Default: PRs are created as drafts.

Examples:
- `/split 3` → N=3, base=auto, draft PRs
- `/split --base develop` → N=auto, base=develop, draft PRs
- `/split 4 --base main` → N=4, base=main, draft PRs
- `/split 2 --no-draft` → N=2, base=auto, ready-for-review PRs

---

## Phase 1: Pre-flight Checks

Run these checks **in order**. Abort with a clear message if any fail.

```bash
# 1. Verify git repo
git rev-parse --git-dir

# 2. Check for clean worktree (no uncommitted changes, no untracked files)
git diff --quiet && git diff --cached --quiet
# Also check for untracked files that could be accidentally staged
git ls-files --others --exclude-standard

# 3. Get current branch name (fails in detached HEAD)
CURRENT_BRANCH=$(git symbolic-ref --short HEAD)
# If this fails: "You are in detached HEAD state. Please checkout a named branch first."

# 4. Detect base branch (if not provided)
# Try: main, master — whichever exists on the remote
BASE_BRANCH=${provided_base:-$(git branch -r | grep -oE 'origin/(main|master)' | head -1 | sed 's|origin/||')}
# If BASE_BRANCH is empty: "Could not detect base branch. Use --base <branch> to specify."

# 5. Guard: current branch must not be the base branch
# If CURRENT_BRANCH == BASE_BRANCH: "You are on the base branch. Switch to a feature branch first."

# 6. Verify split_diff.py exists
SPLIT_SCRIPT="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/skills/split}/scripts/split_diff.py"
# If not found: "split_diff.py not found at $SPLIT_SCRIPT. Is the skill installed?"

# 7. Verify gh CLI is available (needed for Phase 6)
command -v gh
# If missing: warn "gh CLI not found — branches will be created but PRs must be created manually."

# 8. Find merge base
MERGE_BASE=$(git merge-base HEAD "origin/$BASE_BRANCH")

# 9. Verify there are changes to split
git diff --name-only "$MERGE_BASE"..HEAD
# If empty: "No changes found between $CURRENT_BRANCH and $BASE_BRANCH."
```

If untracked files are present, tell the user: "Untracked files detected. These could be accidentally included. Please commit, stash, or .gitignore them first." Then list the files.

If the worktree is dirty, tell the user: "Working tree has uncommitted changes. Please commit or stash them first."

Print a summary:
```
Branch:     $CURRENT_BRANCH
Base:       $BASE_BRANCH
Merge base: ${MERGE_BASE:0:8}
Script:     $SPLIT_SCRIPT
gh CLI:     available / not found (PRs will be skipped)
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
git diff "$MERGE_BASE"..HEAD | python3 "$SPLIT_SCRIPT" analyze
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

**Branch naming:** Derive from the current branch name by appending `-{number}-{short-name}`.
- `Iron-Ham/big-feature` → `Iron-Ham/big-feature-1-models`, `Iron-Ham/big-feature-2-api`
- `feature/auth` → `feature/auth-1-models`, `feature/auth-2-routes`

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

### Setup:

Create a unique temp directory for this run:
```bash
SPLIT_TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/split-XXXXXXXX")
```

### Execution algorithm:

```
For each group K (1 to N):
  1. Start from merge base:
     git checkout -b "$BRANCH_NAME" "$MERGE_BASE"
     # If this fails (e.g., branch already exists), abort with rollback guidance.

  2. For each file in groups 1..K (cumulative):
     Apply changes based on type (see table below)
     # Check exit code after EVERY git checkout / git rm / python3 command.
     # On any failure, abort and print rollback guidance.

  3. Stage specific files and commit:
     git add <file1> <file2> ...   # explicit files only, NOT git add -A
     git commit -m "$COMMIT_MESSAGE"
     # Use conventional commit format (e.g., "feat: ...", "refactor: ...")
```

**Error checking is mandatory.** After every git or python3 command, verify it succeeded. If any step fails, immediately jump to the On Failure section below.

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

For each file needing partial hunks in group K, reconstruct it using the temp directory:

```bash
# Get base file content
git show "$MERGE_BASE:$FILE_PATH" > "$SPLIT_TMPDIR/base_file" || { echo "Error: ..."; exit 1; }

# Get the diff for this file
git diff "$MERGE_BASE".."$ORIGINAL_BRANCH" -- "$FILE_PATH" > "$SPLIT_TMPDIR/file_diff" || { echo "Error: ..."; exit 1; }

# Reconstruct with hunks from groups 1..K
python3 "$SPLIT_SCRIPT" reconstruct \
  --base-file "$SPLIT_TMPDIR/base_file" \
  --diff-file "$SPLIT_TMPDIR/file_diff" \
  --hunks 0,1,3,5 \
  --output "$FILE_PATH" || { echo "Error: reconstruction failed for $FILE_PATH"; exit 1; }
```

The `--hunks` flag takes a comma-separated list of 0-based hunk indices to apply.

### After creating all branches:

Verify the final stacked branch matches the original:
```bash
git diff "$LAST_BRANCH" "$ORIGINAL_BRANCH" --stat
```
If this shows differences, something went wrong during reconstruction. Warn the user and print rollback guidance.

Return to the original branch:
```bash
git checkout "$ORIGINAL_BRANCH"
```

Clean up the temp directory:
```bash
rm -rf "$SPLIT_TMPDIR"
```

### On failure:

If any step fails:
1. Print which branches were created
2. Print cleanup commands: `git branch -D $BRANCH1 $BRANCH2 ...`
3. Return to the original branch
4. **Never auto-delete branches**

---

## Phase 6: Push, Create PRs, and Summarize

**Skip PR creation if `gh` CLI was not found in Phase 1.** In that case, push branches and print manual PR creation commands instead.

### 6a. Push all branches

Push each branch, checking for errors:

```bash
git push -u origin "$BRANCH_1" || echo "Warning: failed to push $BRANCH_1"
git push -u origin "$BRANCH_2" || echo "Warning: failed to push $BRANCH_2"
# ... for each branch
```

If any push fails, report which branches were pushed successfully and which failed, then continue with the branches that did push.

### 6b. Create PRs

Create a PR for each branch. **PRs are drafts by default** unless `--no-draft` was passed.

- The first PR targets the base branch (e.g., `main`).
- Each subsequent PR targets the previous stacked branch.
- PR titles should be human-readable descriptions (no conventional commit prefixes).
- PR bodies must include `## Summary`, `## Stack`, and `## Test plan` sections.

```bash
# Determine draft flag
DRAFT_FLAG="--draft"  # omit if --no-draft was passed

# PR 1 targets base
gh pr create $DRAFT_FLAG --base main --head "$BRANCH_1" \
  --title "Add user and auth data models" \
  --body "$(cat <<'EOF'
## Summary
- First in a stack of N PRs from `$ORIGINAL_BRANCH`
- <describe changes>

## Stack
1. **This PR** ← `$BRANCH_1`
2. TBD ← `$BRANCH_2`

## Test plan
- [ ] <describe how to verify these changes>
EOF
)"

# PR 2 targets PR 1's branch
gh pr create $DRAFT_FLAG --base "$BRANCH_1" --head "$BRANCH_2" \
  --title "Add authentication API routes" \
  --body "$(cat <<'EOF'
## Summary
- Second in a stack of N PRs from `$ORIGINAL_BRANCH`
- <describe changes>

## Stack
1. #PR1_NUMBER ← `$BRANCH_1`
2. **This PR** ← `$BRANCH_2`

## Test plan
- [ ] <describe how to verify these changes>
EOF
)"
```

After creating all PRs:
1. **Update earlier PR bodies** with actual PR numbers/URLs for the stack list.
2. If any `gh pr create` fails, report the error and print the equivalent manual command.

### 6c. Print summary

```
## Split Complete!

Created N stacked branches from `$ORIGINAL_BRANCH`:

| # | Branch | PR | Status |
|---|--------|----|--------|
| 1 | $BRANCH_1 | #1 (draft) | ✓ |
| 2 | $BRANCH_2 | #2 (draft) | ✓ |

Original branch `$ORIGINAL_BRANCH` is unchanged.
```

---

## Reminders

- **NEVER modify the original branch.** All work happens on new branches created from the merge base.
- **ALWAYS wait for user approval in Phase 4.** Never skip ahead.
- **ALWAYS check exit codes** after every git and python3 command. Abort on failure.
- **Cumulative stacking:** Branch K contains changes from groups 1 through K.
- **Hunk indices are 0-based** in split_diff.py.
- **Use explicit file staging** (`git add <files>`) — never `git add -A`.
- **Use conventional commit format** for commit messages (e.g., `feat:`, `fix:`, `refactor:`).
- If N is not specified, choose a reasonable number (typically 2-5) based on the logical structure of the changes.
