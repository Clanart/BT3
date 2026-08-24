### Title
Stale line-index diff selection lets the working directory diff drift out from under a partial-commit selection, silently committing different lines than the user approved - (File: app/src/lib/stores/app-store.ts, app/src/models/diff/diff-selection.ts, app/src/lib/git/apply.ts)

### Summary
The underlying bug class in the report is: the system enforces an aggregate invariant ("TR decreases, TV/TR ratio improves") but never validates the semantic invariant that actually matters to the user ("my position stays long/short"), so a value that looks safe by the checked metric can silently flip an attribute the user never consented to change. GitHub Desktop has a structurally identical pattern in its partial-commit ("stage selected lines") feature: line selection is tracked purely by **numeric line index** into a diff, and when the diff is refreshed the code only checks that a previously-selected index is still "includeable" — not that it still represents the same logical change. This lets the set of lines that actually get staged/committed silently diverge from the set of lines the user reviewed and clicked on.

### Finding Description
`DiffSelection` (app/src/models/diff/diff-selection.ts:53-136) stores selection state as a `Set<number>` of `divergingLines`, i.e. raw indexes into the unified diff, with no reference to the line content or hash.

When the Changes view's diff is reloaded (e.g. because Desktop's filesystem watcher detects the file changed on disk while the user is still working in the Changes pane), `updateChangesWorkingDirectoryDiff` in app-store.ts:3404-3497 recomputes the diff and reapplies the *old* selection to the *new* diff purely by re-validating index membership: [1](#0-0) 

The comment in the code itself acknowledges the gap: *"The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid by ensuring that selected lines still exist but for now we'll settle on just updating the selectable lines..."* — i.e., exactly the kind of unchecked invariant the audit report flags: a narrow condition (index still selectable) is verified while the real invariant (same logical content at that index) is not.

At commit/stage time, `applyPatchToIndex` (app/src/lib/git/apply.ts:12-84) re-fetches the diff fresh from disk and builds the patch strictly from `file.selection.isSelected(absoluteIndex)` (see `formatPatch`, app/src/lib/patch-formatter.ts:129-221), with no re-confirmation from the user and no diffing against what was displayed when the selection was made. So whatever hunks/lines now occupy those same numeric indexes — regardless of whether they are the same edits the user looked at — are what gets staged and committed.

### Impact Explanation
If the working tree file changes between the time a user makes a partial line selection and the time the commit is executed (a realistic window: Desktop live-refreshes diffs via its file watcher, and the "Changes" list/diff persists across such refreshes without forcing the user to re-review), the set of staged lines can silently shift to different content than what the user selected and visually approved. This is a "silent corruption of what the user commits" — the same class of impact called out as valid in this analysis: the committed artifact no longer matches user intent, without any warning, confirmation dialog, or diff invalidation. An attacker who can cause the working tree to change during this window (e.g., a git hook such as `post-checkout`/`post-merge` shipped in a malicious cloned/fetched repository, or an automatically-invoked build/format tool triggered by opening the repo) can exploit this gap to get unintended content committed under the user's authorship.

### Likelihood Explanation
Moderate. It requires (a) the user to have made a partial (not "select all") line selection, and (b) the working tree file to change on disk before the commit is executed. Both are realistic in normal Desktop usage patterns (partial staging is a core, commonly used feature; Desktop actively watches the filesystem and reacts to external changes), and the trigger for (b) can come directly from repository-controlled automation (git hooks, build scripts) without any unusual user action. The comment in the code shows the maintainers were already aware selection revalidation was incomplete, but it has not been fully addressed for the identity-of-content case (only the "line no longer exists" case is handled).

### Recommendation
Track partial selections by content-stable identity rather than raw index — e.g., anchor selection to hunk context (surrounding unchanged lines) or a content hash of the selected line, and invalidate (not silently remap) any selection whose anchor no longer matches when the diff is refreshed. At minimum, when a refreshed diff for a file with a non-trivial (`Partial`) selection is detected, force the file's selection back to `None`/prompt the user to re-review before allowing it to be committed, analogous to how the confirmed Starknet fix adds an explicit guard (`_validate_imposed_reduction_trade`-style check) rather than relying on an aggregate condition to implicitly preserve an invariant it was never designed to protect.

### Proof of Concept
1. Open a repository in Desktop and modify a tracked file with two independent hunks (hunk A at top, hunk B at bottom).
2. In the Changes view, select only hunk A's lines for commit (partial selection), leaving the diff pane open.
3. While still on this screen (before pressing Commit), have an external process — simulating a malicious repo's git hook or auto-run build step — modify the file such that new lines are inserted above hunk A, shifting line numbers, while keeping the total line count coincidentally similar (or simply causing hunk boundaries to shift) so that the previously selected indexes now overlap different hunk content.
4. Desktop's file watcher triggers `updateChangesWorkingDirectoryDiff`, which reapplies the old `divergingLines` index set to the new diff via `withSelectableLines` (app/src/models/diff/diff-selection.ts) without checking the content at those indexes.
5. Click Commit. `applyPatchToIndex`/`formatPatch` (app/src/lib/git/apply.ts, app/src/lib/patch-formatter.ts) generate the patch from the stale index-based selection against the new diff, staging/committing lines the user never actually reviewed or intended to include. [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** app/src/models/diff/diff-selection.ts (L121-136)
```typescript
  /** Returns a value indicating wether the given line number is selected or not */
  public isSelected(lineIndex: number): boolean {
    const lineIsDivergent =
      !!this.divergingLines && this.divergingLines.has(lineIndex)

    if (this.defaultSelectionType === DiffSelectionType.All) {
      return !lineIsDivergent
    } else if (this.defaultSelectionType === DiffSelectionType.None) {
      return lineIsDivergent
    } else {
      return assertNever(
        this.defaultSelectionType,
        `Unknown base selection type ${this.defaultSelectionType}`
      )
    }
  }
```

**File:** app/src/lib/git/apply.ts (L12-84)
```typescript
export async function applyPatchToIndex(
  repository: Repository,
  file: WorkingDirectoryFileChange
): Promise<void> {
  // If the file was a rename we have to recreate that rename since we've
  // just blown away the index. Think of this block of weird looking commands
  // as running `git mv`.
  if (file.status.kind === AppFileStatusKind.Renamed) {
    // Make sure the index knows of the removed file. We could use
    // update-index --force-remove here but we're not since it's
    // possible that someone staged a rename and then recreated the
    // original file and we don't have any guarantees for in which order
    // partial stages vs full-file stages happen. By using git add the
    // worst that could happen is that we re-stage a file already staged
    // by updateIndex.
    await git(
      ['add', '--update', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    // Figure out the blob oid of the removed file
    // <mode> SP <type> SP <object> TAB <file>
    const oldFile = await git(
      ['ls-tree', 'HEAD', '--', file.status.oldPath],
      repository.path,
      'applyPatchToIndex'
    )

    const [info] = oldFile.stdout.split('\t', 1)
    const [mode, , oid] = info.split(' ', 3)

    // Add the old file blob to the index under the new name
    await git(
      ['update-index', '--add', '--cacheinfo', mode, oid, file.path],
      repository.path,
      'applyPatchToIndex'
    )
  }

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

  return Promise.resolve()
}
```
