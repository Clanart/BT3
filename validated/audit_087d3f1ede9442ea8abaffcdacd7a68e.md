### Title
Partial-commit staging re-diffs the file at apply time, letting stale line-selection indices be applied to a different (attacker-modified) diff and silently commit/push unintended content - (File: `app/src/lib/git/apply.ts`)

### Summary
The sequencer-uptime-feed report's broken invariant is: **a decision surface (price/collateral state) is captured at one point in time, but the action that consumes it (liquidation) is executed later against data that has since changed, with no re-validation and no grace period for the user to notice.** The Desktop analog is the partial-commit ("select lines to include") flow: the user reviews a diff and selects specific lines to stage, but `applyPatchToIndex` re-fetches a *fresh* diff of the file at staging time and blindly re-applies the user's old line-index selection to it, with no check that the diff is still the one the user reviewed.

### Finding Description
When a user stages a subset of lines from a modified file, `Changes`/`sidebar.tsx` builds a `WorkingDirectoryFileChange` whose `selection` stores **absolute line indices** into the diff that was rendered in the UI [1](#0-0) .

At commit time, `createCommit` calls `stageFiles`, which for any file with a partial selection calls `applyPatchToIndex` [2](#0-1) . Critically, `applyPatchToIndex` does **not** reuse the diff the user actually looked at — it re-runs `getWorkingDirectoryDiff(repository, file)` right before building the patch: [3](#0-2) 

The freshly-fetched diff's hunks are then combined with the file's *old* `selection` (the line indices computed against the previously-rendered diff) inside `formatPatch`, which walks `diff.hunks` and simply asks `file.selection.isSelected(absoluteIndex)` for each line — there is no comparison between the diff that produced the selection and the diff being patched: [4](#0-3) 

The resulting patch is applied with `git apply --cached --unidiff-zero`, which matches hunks purely by line offsets rather than by content context (`--unidiff-zero` means zero lines of context), so there is no git-level safety net that would reject a patch whose "shape" no longer matches the file: [5](#0-4) 

This mirrors the sequencer bug's structure exactly: (1) a value is captured (price / line selection), (2) time passes during which the underlying source can change (sequencer outage / working-tree file mutation), (3) the stale value is consumed all at once without a "grace period" or re-validation (mass liquidation processing / blind re-application of line indices to a new diff), and (4) an existing partial guard elsewhere (`updateChangesWorkingDirectoryDiff`'s comment explicitly acknowledges "the diff might have changed dramatically since last we loaded it" and reconciles *selectable lines* for UI display) does **not** propagate to the actual staging/patch-application code path used by `createCommit`/`applyPatchToIndex` [6](#0-5) .

### Impact Explanation
If the tracked file's content changes between the moment the user reviews/selects lines in the Changes view and the moment they click "Commit" (e.g. because the cloned/fetched repository ships a build tool, linter/formatter, or a git hook that mutates working-tree files on checkout/fetch — content fully controlled by whoever authored the repository the user cloned), the commit that gets created can silently contain different lines/content than what the user reviewed and explicitly opted into. This is exactly the "silent corruption of what the user commits or pushes" impact category: the user believes they excluded certain lines (e.g. a secret, a debug statement, or unreviewed code) but the stale index selection, applied against the new diff shape, may include them anyway (or vice versa exclude changes the user meant to keep), and the change is pushed without any warning dialog.

### Likelihood Explanation
The time-of-check/time-of-use gap between diff-render and commit-apply is inherent to the UI flow (users often take seconds to write a commit message before pressing "Commit"), and Desktop repositories commonly integrate hooks/build tooling that can touch tracked files during this window; the `.git/hooks` interception in `core.ts`/`with-hooks-env.ts` shows hooks are a first-class, actively-exercised feature of the app rather than a hypothetical, making the file-mutation trigger realistic rather than contrived [7](#0-6) . No user action beyond normal use (select lines, wait, then commit) is required, and no local/physical access or leaked credentials are needed — only an attacker-authored repository.

### Recommendation
Before calling `formatPatch`/`applyPatchToIndex`, re-diff the file and compare it (e.g., by hash of the diff text/hunks) against the diff that was current when the selection was made; if they differ, refuse to stage/commit the file and surface a dialog forcing the user to re-review the new diff (similar in spirit to the existing `LocalChangesOverwrittenDialog` pattern) rather than silently reapplying stale line indices to new content.

### Proof of Concept
1. Clone a repository that includes a Husky/lint-staged style pre-commit hook or file-watcher script that appends/removes lines from a tracked file shortly after it's touched (fully attacker-controlled content in the cloned repo).
2. In Desktop, modify `file.txt` and open the diff; select only lines 1–5 for inclusion (leave lines 6–10, containing e.g. `console.log("debug")`, unselected).
3. Before clicking "Commit", let the repository's background tooling rewrite `file.txt` so that new content is inserted at the top, shifting all line offsets.
4. Click "Commit". `applyPatchToIndex` re-fetches the diff (now shifted) and applies the original `DiffSelection` (still targeting the old absolute indices) via `git apply --cached --unidiff-zero` [8](#0-7) .
5. Inspect the resulting commit: it contains different line content than what was visually selected in step 2 (e.g., the debug line the user deliberately excluded is now staged, or intended lines are missing) — with no warning shown to the user before the commit/push completed.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3444-3497)
```typescript
    const diff = await getWorkingDirectoryDiff(
      repository,
      selectedFileBeforeLoad,
      this.hideWhitespaceInChangesDiff
    )

    const stateAfterLoad = this.repositoryStateCache.get(repository)
    const changesState = stateAfterLoad.changesState

    // A different file (or files) could have been selected while we were
    // loading the diff in which case we no longer care about the diff we
    // just loaded.
    if (
      changesState.selection.kind !== ChangesSelectionKind.WorkingDirectory ||
      !arrayEquals(
        changesState.selection.selectedFileIDs,
        selectedFileIDsBeforeLoad
      )
    ) {
      return
    }

    const selectedFileID = changesState.selection.selectedFileIDs[0]

    if (selectedFileID !== selectedFileIdBeforeLoad) {
      return
    }

    const currentlySelectedFile =
      changesState.workingDirectory.findFileWithID(selectedFileID)
    if (currentlySelectedFile === null) {
      return
    }

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

**File:** app/src/lib/git/apply.ts (L52-81)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

**File:** app/src/lib/patch-formatter.ts (L143-172)
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
      } else {
        // Unselected lines in new files needs to be ignored. A new file by
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
