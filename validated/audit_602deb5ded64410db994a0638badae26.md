## Analysis

The report's broken invariant is: **a value derived from untrusted/volatile external state (AMM output) is trusted and executed without re-validating it matches the state the caller actually reviewed/approved.** The strongest Desktop analog to this pattern is in the partial-commit ("stage selected lines") pipeline, where a `DiffSelection` computed from one diff snapshot is blindly reapplied to a *freshly re-fetched* diff of the working directory at staging time, with no check that the two correspond to the same content.

### Title
Stale diff selection reused when staging partial commits allows silently committing/pushing unreviewed working-directory content — (File: `app/src/lib/git/apply.ts`)

### Summary
When a user stages only some lines of a file ("partial commit"), Desktop keeps a `DiffSelection` object indexing lines by their position in the diff the user was shown. At actual commit time, `applyPatchToIndex` re-fetches a brand-new diff of the working directory and then reapplies the *old* selection's line indices to that new diff to build the patch that gets applied to the index. There is no check that the new diff is structurally identical to the one the selection was built against.

### Finding Description
`applyPatchToIndex` fetches the diff fresh right before staging and formats a patch using the caller-supplied `file.selection`: [1](#0-0) 

`formatPatch`/`formatPatchToDiscardChanges` decide which lines to include purely by `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex` is derived from the hunk positions of *whichever* diff is passed in: [2](#0-1) 

The selection itself is set earlier, based on the diff that was loaded and rendered to the user (often much earlier, while the user was clicking individual lines/hunks). The commit path pulls `file.selection` straight from cached `workingDirectory.files` state and passes it through `createCommit` → `stageFiles` → `applyPatchToIndex`: [3](#0-2) [4](#0-3) 

The app does have logic to prune selections against a newly-loaded diff, but it only runs when the Changes view explicitly reloads a diff (`updateChangesDiffForCurrentSelection`), not synchronously immediately before every staging/commit operation: [5](#0-4) 

If the working-directory file is modified between the moment the user made their line selection and the moment `_commitIncludedChanges`/`stageFiles` actually runs (e.g., a build tool, formatter, watch task, git smudge/clean filter, or any other process with write access to the checkout mutates the file in that window), the hunk boundaries and `unifiedDiffStart` offsets of the new diff computed inside `applyPatchToIndex` can differ from the diff the selection indices were computed against. Because there is no comparison/validation between the two diffs (no hash, no hunk-shape check), the same numeric line indices now point at semantically different lines. This can cause the committed patch to include lines the user never selected/reviewed, or omit lines the user did select — a silent divergence between what the UI showed as "selected for commit" and what is actually staged and committed.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." A user might explicitly deselect a suspicious/malicious line change from an untrusted collaborator's working tree modification (or from content written concurrently by a compromised local tool/hook operating on the cloned repository), believing they excluded it, yet due to the index-based reapplication of a stale selection, that content could still end up staged and committed under the user's identity — or conversely, content the user intended to commit could be silently dropped. There is no cryptographic/hash validation tying the reviewed diff to the diff actually patched.

### Likelihood Explanation
This requires a concurrent modification to the working directory file within the window between UI-side line selection and the user clicking "Commit" — which is a normal occurrence with file watchers, formatters, linters, or build tools that many repositories configure to run automatically, and is entirely plausible for repos an attacker controls (a cloned/fetched malicious repository can ship tooling/hooks/scripts that legitimately run in that window). It does not require local/admin access beyond what Desktop already grants the working tree's configured tooling, and requires no unnatural user steps beyond normal partial-line staging usage.

### Recommendation
Before applying `file.selection` to a freshly-fetched diff in `applyPatchToIndex`, validate that the new diff is structurally compatible with the diff the selection was computed against (e.g., compare a hash of the diff text/hunks, or recompute `selectableLines` and reject/re-derive the selection if hunks changed) rather than blindly trusting stale numeric indices, mirroring the pruning logic already present in `updateChangesDiffForCurrentSelection` but enforced synchronously at staging time.

### Proof of Concept
1. Open a modified tracked file in Desktop's Changes view; the diff loads and the user deselects a specific line/hunk (`DiffSelection` built against diff v1).
2. Before clicking "Commit," an external process (a configured watch/format script, or any process with write access to the working copy) modifies the file such that line offsets shift (e.g., inserts/removes lines above the target hunk) without Desktop's UI re-rendering the diff yet.
3. User clicks "Commit." `_commitIncludedChanges` → `createCommit` → `stageFiles` → `applyPatchToIndex` fetches diff v2 fresh via `getWorkingDirectoryDiff`, but reuses the v1-based `DiffSelection` object's `isSelected(absoluteIndex)` checks against v2's hunks.
4. Because hunk offsets differ between v1 and v2, the resulting patch built by `formatPatch` includes/excludes different logical lines than what the user actually selected, and this patch is applied to the index and committed without any warning. [1](#0-0) [2](#0-1)

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

**File:** app/src/lib/stores/app-store.ts (L3685-3689)
```typescript
    const state = this.repositoryStateCache.get(repository)
    const files = state.changesState.workingDirectory.files
    const selectedFiles = files.filter(file => {
      return file.selection.getSelectionType() !== DiffSelectionType.None
    })
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
