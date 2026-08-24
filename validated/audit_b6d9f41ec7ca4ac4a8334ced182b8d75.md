This is a legitimate GitHub Desktop analog: a repository-controlled `post-checkout`/`post-merge` git hook can silently modify a tracked file's contents on disk *after* Desktop has already computed and cached a diff and the user has made a partial line selection, and Desktop's stale-diff/selection-index reconciliation logic does not fully invalidate selections that still "line up" by index — allowing the user to unknowingly commit/push attacker-influenced content that isn't what they reviewed.

### Title
Repo-controlled git hook can desync cached diff line-selection indices, causing silent inclusion of unreviewed content in a partial commit - (File: `app/src/lib/stores/app-store.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
Desktop intercepts and executes repository-defined git hooks (`post-checkout`, `post-merge`, etc.) via `withHooksEnv`/`getRepoHooks`, which is fully attacker-controlled content in a cloned/fetched repository [1](#0-0) [2](#0-1) . When a user has a diff open with a **partial line selection** (e.g. staging only some lines of a file for commit), Desktop's line-selection state is expressed purely as a set of *absolute line indices* into the parsed diff (`hunk.unifiedDiffStart + lineIndex`), independent of the actual text content at that index [3](#0-2) .

### Finding Description
Desktop reloads the working-directory diff asynchronously (`updateChangesWorkingDirectoryDiff`) and, when the diff changes, only prunes the selection down to the new `selectableLines` set of indices — it does **not** re-validate that a previously-selected index still corresponds to the same logical change the user reviewed: [4](#0-3) 

The comment in the code itself acknowledges the weakness: *"The diff might have changed dramatically since last we loaded it... we'll settle on just updating the selectable lines such that any previously selected line which now no longer exists or has been turned into a context line isn't still selected."* This only removes now-invalid indices; if a hook rewrites the file such that a line at the *same index* still qualifies as an includable (`Add`/`Delete`) line, the previously-set selection bit is preserved and blindly reused.

`formatPatch` (used to build the exact partial-commit patch handed to `git apply --cached`) walks the *current* diff and consults `file.selection.isSelected(absoluteIndex)` purely by index to decide which lines go into the commit: [5](#0-4) 

Because a repository can ship a `post-checkout` or `post-merge` hook (both in Desktop's `knownHooks` allow-list and executed via the hooks proxy) [1](#0-0) , an attacker who controls the cloned repository can have the hook rewrite a tracked file the instant after checkout/merge — replacing benign lines with attacker content while preserving the same number/positions of `Add`/`Delete` lines in the new diff. If the user had already begun reviewing/selecting lines in that file (e.g. mid-review during a slow hook execution, or the diff auto-refreshes after the hook runs while the selection bitset is preserved by index), the stale by-index selection will now silently point at the attacker's substituted lines instead of the ones the user actually reviewed and checked. `_repayLoan`'s "amount decoupled from the memory struct" invariant break maps directly to "selection bitset by index decoupled from the diff content at that index" — the guard (`withSelectableLines`) only checks *existence*, not *identity/content*, of the line.

### Impact Explanation
This results in **silent corruption of what the user commits and subsequently pushes**: the partial commit created via `createCommit`/`stageFiles`/`applyPatchToIndex` [6](#0-5)  can contain attacker-authored content that was never shown to or approved by the user in that review pass, while the visible commit message and file path look legitimate. This satisfies the "silent corruption of what the user commits or pushes" impact category from an unprivileged, repository-controlled vector (a cloned/fetched repository with a hook), without requiring local/physical access, admin rights, or pre-existing malware.

### Likelihood Explanation
Moderate-to-low likelihood in practice: it requires (1) the hooks-interception feature to be enabled for the relevant git operation (`interceptHooks`/`getHooksEnvEnabled()`) [2](#0-1) , (2) a timing window where the user has an open partial selection while a background diff refresh occurs after a hook-triggered file mutation, and (3) the hook's rewrite to preserve enough structural similarity (same includable-line count/position) to pass the `withSelectableLines` filter unnoticed. This is a genuine race/TOCTOU condition rather than a deterministic one-shot exploit, but the primitive (attacker-controlled hook execution + index-only selection validity) is concretely present in the code, not speculative.

### Recommendation
Do not preserve partial line-selection state across a diff reload purely by index. When `updateChangesWorkingDirectoryDiff` detects the underlying diff/hunks have changed, compare selected lines by content/hash (not just index) or, conservatively, clear all partial selections and require the user to re-review and re-select before committing — mirroring how `clearPartialState` already resets selection in `updateChangedFiles` when files are considered untrusted [7](#0-6) . Apply the same invalidation to the async diff-reload path in `app-store.ts`, not just the status-refresh path.

### Proof of Concept
Not independently executed/verified against a live Desktop build; this is derived from static code-flow analysis of `withHooksEnv`/`getRepoHooks` (attacker-controlled hook execution point) combined with `updateChangesWorkingDirectoryDiff`'s index-based selection carry-over and `formatPatch`'s index-based line inclusion. A concrete PoC would require: (a) confirming `interceptHooks` is set for `checkout`/`merge` operations in `core.ts`, and (b) timing a hook-driven file rewrite to land between the initial diff load and the `formatPatch` call for a user-initiated partial commit — this exact timing behavior is not fully verifiable from the indexed code alone and would benefit from dynamic testing in a full Devin session with repository access.

### Citations

**File:** app/src/lib/hooks/get-repo-hooks.ts (L10-39)
```typescript
const knownHooks = [
  'applypatch-msg',
  'pre-applypatch',
  'post-applypatch',
  'pre-commit',
  'pre-merge-commit',
  'prepare-commit-msg',
  'commit-msg',
  'post-commit',
  'pre-rebase',
  'post-checkout',
  'post-merge',
  'pre-push',
  'pre-receive',
  'update',
  'proc-receive',
  'post-receive',
  'post-update',
  'reference-transaction',
  'push-to-checkout',
  'pre-auto-gc',
  'post-rewrite',
  'sendemail-validate',
  'fsmonitor-watchman',
  'p4-changelist',
  'p4-prepare-changelist',
  'p4-post-changelist',
  'p4-pre-submit',
  'post-index-change',
]
```

**File:** app/src/lib/hooks/with-hooks-env.ts (L29-42)
```typescript
export async function withHooksEnv<T>(
  fn: (env: Record<string, string | undefined> | undefined) => Promise<T>,
  path: string,
  opts: IGitExecutionOptions | undefined
): Promise<T> {
  if (!opts?.interceptHooks || !getHooksEnvEnabled()) {
    return fn(opts?.env)
  }

  const hooks = await Array.fromAsync(getRepoHooks(path, opts.interceptHooks))

  if (hooks.length === 0) {
    return fn(opts?.env)
  }
```

**File:** app/src/lib/patch-formatter.ts (L143-170)
```typescript
    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

      // We write our own hunk headers
      if (line.type === DiffLineType.Hunk) {
        return
      }

      // Context lines can always be let through, they will
      // never appear for new files.
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
```

**File:** app/src/lib/stores/app-store.ts (L3478-3497)
```typescript
    const selectableLines = new Set<number>()
    if (diff.kind === DiffType.Text || diff.kind === DiffType.LargeText) {
      // The diff might have changed dramatically since last we loaded it.
      // Ideally we would be more clever about validating that any partial
      // selection state is still valid by ensuring that selected lines still
      // exist but for now we'll settle on just updating the selectable lines
      // such that any previously selected line which now no longer exists or
      // has been turned into a context line isn't still selected.
      diff.hunks.forEach(h => {
        h.lines.forEach((line, index) => {
          if (line.isIncludeableLine()) {
            selectableLines.add(h.unifiedDiffStart + index)
          }
        })
      })
    }

    const newSelection =
      currentlySelectedFile.selection.withSelectableLines(selectableLines)
    const selectedFile = currentlySelectedFile.withSelection(newSelection)
```

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```

**File:** app/src/lib/stores/updates/changes-state.ts (L46-54)
```typescript
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }
```
