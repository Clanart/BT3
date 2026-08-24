## Analysis

The Frax report's broken invariant is: **a value is computed once (for user review/approval) but re-computed and used again at execution time, with no check that the two agree** — allowing the executed operation to silently diverge from what the user approved.

GitHub Desktop has a structural analog to this in how it stages partial commits.

### Title
Partial-commit staging re-derives the diff from disk at apply time instead of the diff the user reviewed, allowing silent line-selection misalignment - (File: `app/src/lib/git/apply.ts`)

### Summary
When a user selects individual lines/hunks of a file to include in a commit, Desktop stores that selection as a set of *absolute line indices* (`DiffSelection`) tied to the diff that was rendered in the UI at selection time. At commit time, `applyPatchToIndex` does not reuse that reviewed diff — it re-runs `git diff` against the working tree to get a brand-new diff, and then applies the stored index-based selection to whatever hunks that fresh diff happens to produce, with no verification that the new diff still matches the one the user looked at.

### Finding Description
`applyPatchToIndex` fetches a fresh diff and immediately formats a patch from it using the (potentially stale) `file.selection`: [1](#0-0) 

`formatPatch` decides what to include purely by absolute line index (`file.selection.isSelected(absoluteIndex)`) against whatever hunks are in the diff it's handed — it has no way to know if those hunks are the same content the user actually reviewed: [2](#0-1) 

`stageFiles` drives this for every partially-selected file during commit creation, again passing the file object (with its stored selection) straight into `applyPatchToIndex` without any diff-identity check: [3](#0-2) 

Compare this to the UI-refresh path, which *does* explicitly acknowledge the risk that on-disk content can change and defensively clears selections for lines that no longer exist — but only while a file is displayed in the Changes list, not at the moment `applyPatchToIndex` actually builds the patch for the commit: [4](#0-3) 

So the only safeguard in the codebase for "diff changed since selection was made" lives in the UI-refresh reducer, not in the git-layer commit path that actually writes to the index. If the file's on-disk content changes between when the user reviewed/selected specific lines and when `git add`/commit executes (e.g. from a build watcher, formatter-on-save, or any tooling launched from the opened project that touches tracked files), `applyPatchToIndex` will silently apply the stored line indices to the new hunk boundaries of the re-fetched diff. This can stage/commit different lines than what the user actually saw and approved — a "what you commit is not what you reviewed" corruption, directly analogous to Frax's "amount paid differs from what was approved, with no check."

### Impact Explanation
This can cause **silent corruption of what the user commits**: content the user never intended to include could be staged and committed (or, conversely, content they meant to include could be silently dropped), because `git apply --cached` will apply the index-based selection to whatever hunks exist in the fresh diff — not the ones the user visually confirmed. This falls under the in-scope impact category of "silent corruption of what the user commits or pushes," since the resulting commit can differ from the reviewed diff without any warning to the user.

### Likelihood Explanation
The likelihood depends on how much a file can move on disk between the moment the user makes a line selection in the Changes view and the moment they click Commit — a window that's realistically seconds while typing a commit message, during which an attacker-influenced repo (e.g. one that ships a watch/format/build script the user has running, or a pre-commit-adjacent local tool) can rewrite the tracked file. There's no dedicated exploit trigger inside Desktop itself forcing the race — it's a latent TOCTOU gap in the git layer, not something requiring local/admin access or credential leakage, but it does require some external process modifying the file at the right moment, which lowers likelihood relative to a fully attacker-triggered path.

### Recommendation
- **Short term:** Before calling `applyPatchToIndex`/`formatPatch`, verify that the diff used to build the patch still matches (e.g. via content hash or hunk fingerprint) the diff the selection was derived from; if it doesn't, abort staging that file and force the UI to refresh/re-prompt the user instead of silently applying stale line indices.
- **Long term:** Thread the exact diff object the user reviewed (not a freshly re-fetched one) through the commit pipeline down into `applyPatchToIndex`, so the patch is always built from the same diff state the user saw and explicitly acted on.

### Proof of Concept
1. Open a repository in Desktop with a tracked file `foo.txt` containing several lines.
2. In the Changes view, edit `foo.txt` to add lines A, B, C and deselect line B (partial selection), leaving A and C selected via `withRangeSelection`, as exercised in [5](#0-4) .
3. Before clicking "Commit", have an external process (e.g. a repo-provided watch/format script) rewrite `foo.txt` so the hunk boundaries shift (e.g. reformat/reflow lines) without the Changes view being refreshed/re-rendered yet.
4. Click Commit. `stageFiles` → `applyPatchToIndex` re-runs `getWorkingDirectoryDiff` against the now-different file and applies the old absolute-index selection to the new hunks via `formatPatch`'s `file.selection.isSelected(absoluteIndex)` check, producing a patch that includes/excludes different content than what was shown and selected in step 2 — with no error or warning shown to the user.

### Citations

**File:** app/src/lib/git/apply.ts (L60-81)
```typescript
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

**File:** app/src/lib/patch-formatter.ts (L143-171)
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

**File:** app/test/unit/git/commit-test.ts (L224-232)
```typescript
      const selection = DiffSelection.fromInitialSelection(
        DiffSelectionType.All
      ).withRangeSelection(
        diff.hunks[0].unifiedDiffStart,
        diff.hunks[0].unifiedDiffEnd - diff.hunks[0].unifiedDiffStart,
        false
      )

      const updatedFile = file.withSelection(selection)
```
