## Title
Stale line-selection indices reused against a freshly re-fetched diff produce silently mismatched partial commits/stages - (File: `app/src/lib/git/apply.ts`)

### Summary
The Sherlock report's broken invariant is: a "tolerance/verification" value (`minRSETHAmountExpected`) is set to `0`, so the protocol blindly accepts whatever an external, attacker/market-influenced value returns instead of checking it still matches what was intended when the transaction was built, silently minting less than expected. The Desktop analog is `applyPatchToIndex` in [1](#0-0) , which builds the patch to stage/commit by combining a **freshly re-fetched** diff (`getWorkingDirectoryDiff`) with the file's **previously computed** `selection` bitmap (line indices chosen by the user against an *older* diff), with no check that the two are still consistent.

### Finding Description
`stageFiles`/`applyPatchToIndex` is the code path used both for partial staging and for `createCommit` with partially selected files [2](#0-1) . Inside `applyPatchToIndex`, the diff used to build the patch is fetched at apply time: `const diff = await getWorkingDirectoryDiff(repository, file)` [3](#0-2) , then `formatPatch(file, diff)` walks `diff.hunks` and decides which lines to include using `file.selection.isSelected(absoluteIndex)` where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` [4](#0-3) .

The `selection` object, however, is state computed earlier when the user reviewed the diff in the UI and clicked/dragged to select lines — it is **not guaranteed to be based on the same diff object** that `getWorkingDirectoryDiff` returns at commit time. Desktop's own code acknowledges this staleness risk in the UI diff-refresh path: "The diff might have changed dramatically since last we loaded it. Ideally we would be more clever about validating that any partial selection state is still valid... but for now we'll settle on just updating the selectable lines" [5](#0-4) . This reconciliation only runs when the Changes-tab diff view refreshes; it is not re-run/re-verified inside `applyPatchToIndex` immediately before the patch is generated and applied with `git apply --cached --unidiff-zero --whitespace=nowarn -` [6](#0-5) .

If the working-tree file content changes between the moment the user made a line selection and the moment `createCommit`/staging actually runs — e.g. because a `post-checkout`/`post-merge`/`post-fetch` hook from a cloned/fetched repository rewrites tracked files, or an external tool/editor touches the file — the newly fetched diff's hunk boundaries and line ordering can shift. The same numeric `absoluteIndex` values in the stale selection bitmap will then be applied against different lines in the new hunk layout. Because `--unidiff-zero` permits hunks that carry no (or minimal) surrounding context and `--whitespace=nowarn` suppresses whitespace-mismatch warnings, `git apply` will not reject a patch whose content doesn't match the user's original visual review — it silently stages/commits whatever `formatPatch` produced from the mismatched indices.

This is structurally identical to the audited bug: a value that is supposed to gate on "does this still match what we expected" (there, `minRSETHAmountExpected`; here, "is `selection` still valid for `diff`") is not checked, so a value computed against one state gets applied to a different, more current state without any reconciliation or abort.

### Impact Explanation
The result is that Desktop can silently commit or stage content the user did not actually select — wrong hunks, wrong lines, or lines that no longer correspond to what was shown in the diff viewer — without any error or warning. This is a form of silent corruption of what the user commits/pushes, directly matching the "Valid Impact" criteria (attacker-influenced repository content via clone/fetch/hooks leading to corruption of committed content), without requiring local/physical access, admin rights, or pre-existing malware beyond the untrusted repository content itself.

### Likelihood Explanation
Requires a timing window between diff generation/selection and staging where the tracked file's content changes (e.g., via a hook triggered by cloning/fetching/checking out an attacker-supplied repository, or a build/format-on-save tool). Desktop already documents that diffs can change "dramatically" between load and use [5](#0-4) , indicating the underlying race is acknowledged as realistic, just not hardened against on the commit-time code path.

### Recommendation
Before generating the patch in `applyPatchToIndex`, re-validate that `file.selection`'s selectable-line set still matches the freshly fetched `diff` (recomputing selectable lines the same way `app-store.ts` does for the UI), and abort/re-prompt the user rather than silently applying a patch built from a possibly-stale selection. Consider replacing `--unidiff-zero`/`--whitespace=nowarn` with a stricter `git apply --check` dry run (or comparing the post-apply staged diff against the intended selection) so any mismatch fails loudly instead of silently committing different content than what the user reviewed.

### Proof of Concept
1. Attacker provides a repository (via clone URL, or a branch fetched/checked out by the victim) containing a `post-checkout` (or similar) hook that rewrites a tracked file's line ordering/content shortly after checkout.
2. Victim opens the Changes tab in Desktop, reviews the diff for that file, and selects specific lines for a partial commit (building a `DiffSelection` bitmap keyed to line indices of the currently displayed diff).
3. Before the victim clicks "Commit," the hook (or any other background process) modifies the file, shifting hunk boundaries.
4. Victim commits. `createCommit` → `stageFiles` → `applyPatchToIndex` re-fetches the diff (now shifted) but reuses the old `selection` bitmap indices [3](#0-2) [4](#0-3) .
5. `git apply --unidiff-zero --whitespace=nowarn` accepts the resulting patch without validating it reflects the user's actual reviewed intent, and the commit silently contains different content than what was shown/selected in the UI.

### Citations

**File:** app/src/lib/git/apply.ts (L52-60)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)
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

**File:** app/src/lib/patch-formatter.ts (L143-157)
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
```

**File:** app/src/lib/stores/app-store.ts (L3480-3486)
```typescript
      // The diff might have changed dramatically since last we loaded it.
      // Ideally we would be more clever about validating that any partial
      // selection state is still valid by ensuring that selected lines still
      // exist but for now we'll settle on just updating the selectable lines
      // such that any previously selected line which now no longer exists or
      // has been turned into a context line isn't still selected.
      diff.hunks.forEach(h => {
```
