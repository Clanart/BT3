I have enough evidence now. Let me lay out the finding.

### Title
Line-index-based partial commit staging trusts a stale diff snapshot, allowing a repository-controlled working-tree race to silently commit/discard unintended lines - (`app/src/lib/git/apply.ts`)

### Summary
GitHub Desktop's partial-file staging feature lets a user select individual diff lines to include in a commit. The selection is recorded purely as numeric line offsets (`DiffSelection`) against a specific `ITextDiff` snapshot shown in the UI. When the commit is actually executed, `applyPatchToIndex` re-fetches a *fresh* diff of the file and re-applies the stored line-offset selection to that new diff with no verification that the file's on-disk content still matches what the user was looking at when they made the selection. This is structurally the same "trust a dynamic external value at execution time without validating it matches what the user consented to" flaw described in the Securitize report (missing check between quote/selection and settlement), just manifested as a stale-diff race instead of a stale-price race.

### Finding Description
The commit flow is:
1. UI loads a diff and lets the user toggle individual lines/hunks. This state lives in `WorkingDirectoryFileChange.selection`, addressed purely by absolute line index (`hunk.unifiedDiffStart + lineIndex`), not by content hash or context.
2. `app-store.ts`'s `updateChangesWorkingDirectoryDiff` explicitly acknowledges the diff can go stale ("The diff might have changed dramatically since last we loaded it...") and only prunes indices that no longer exist — it does not invalidate the commit or ask the user to re-confirm. [1](#0-0) 
3. At commit time, `stageFiles` calls `applyPatchToIndex` for every partially-selected file. [2](#0-1) 
4. `applyPatchToIndex` fetches yet another fresh `getWorkingDirectoryDiff` at the moment of staging and rebuilds a patch from the (old, UI-derived) `file.selection` against this newest diff. [3](#0-2) 
5. `formatPatch` maps the selection back onto the new diff purely via `file.selection.isSelected(absoluteIndex)` — an integer offset comparison with no content/context validation. [4](#0-3) 
6. The resulting patch is applied with `git apply --cached --unidiff-zero`, which is deliberately configured to ignore surrounding context and apply the exact line numbers given, so `git apply` itself performs no independent sanity check that the target lines still correspond to what was originally shown to the user. [5](#0-4) 

The broken invariant: "the set of lines the user visually selected == the set of lines actually staged/committed" is assumed to hold across an unbounded time window (the time the user spends reviewing the diff, typing a commit message, and clicking Commit), during which the working-tree file can be modified out from under the app — e.g. by build/watch scripts, editor auto-formatters, git hooks, or any other process the *cloned repository itself* triggers (postinstall scripts, `.vscode` tasks, file watchers a malicious repo instructs the user to run) — none of which require local/physical access or prior malware, only that the user opened/interacted with a hostile-authored repository. Nothing in `applyPatchToIndex`, `formatPatch`, or `stageFiles` re-diffs against the exact `ITextDiff` the user's selection was built from, nor checks a hash/mtime of the file to detect drift.

### Impact Explanation
If the file's line layout shifts between the diff being rendered and the patch being generated, `absoluteIndex`-based selection can silently map to different content than what the user saw and approved. This can cause GitHub Desktop to stage/commit lines the user never selected, or omit lines the user did select, with no error and no diff re-confirmation shown before the commit is finalized — directly matching the "silent corruption of what the user commits or pushes" impact class. Because `--unidiff-zero` disables git's normal context-based safety check, `git apply` will not reject a patch just because surrounding lines changed, so the corruption is not merely blocked by git's own hunk-context matching.

### Likelihood Explanation
Requires a repository under attacker influence (matching the allowed "cloned/fetched repository" primitive) that runs code capable of touching tracked files after the user opens it in Desktop — e.g. a project with an npm/yarn lifecycle script, a file watcher, editor task, or any tooling the README instructs the victim to run while GitHub Desktop is open and a partial-commit selection is in progress. The window is the ordinary time a developer spends reviewing/typing a commit message, which is realistic and requires no unnatural user steps beyond normal use of Desktop.

### Recommendation
- Bind `DiffSelection` to an identifier of the exact diff content it was computed from (e.g., a hash of the diff text or of the underlying blob/mtime) rather than raw integer offsets.
- Before staging, re-fetch the diff and, if it differs from the diff the selection was generated against, refuse to commit and force the UI to reload the diff and require the user to reselect/confirm.
- Avoid `--unidiff-zero`, or supplement it with an explicit check that the hunk context (not just line counts) still matches the working tree before applying.

### Proof of Concept
1. Open a repository in GitHub Desktop containing a file `foo.txt` with several lines.
2. Modify `foo.txt` and open the Changes view; partially select only lines 10–12 for the commit (leaving other changed lines unselected). The UI computes `DiffSelection` against diff snapshot D1.
3. While the commit message is being typed (before clicking "Commit"), a background process from the repository (e.g., a `postinstall`/watch script, or a task the malicious repo's docs told the user to run) rewrites `foo.txt`, shifting line offsets (e.g., inserting/removing lines above the selected hunk) without changing the working-directory selection state.
4. User clicks "Commit". `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches diff D2 (reflecting the tampered file) and applies the old D1-based line-offset selection to D2 via `formatPatch`/`git apply --unidiff-zero`. [6](#0-5) 
5. The resulting commit contains different lines than the ones the user visually reviewed and selected, with no warning, silently corrupting the committed content.

### Citations

**File:** app/src/lib/stores/app-store.ts (L3478-3493)
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
