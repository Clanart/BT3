Based on extensive searching, this codebase has been heavily hardened against most known GitHub Desktop attack classes I could think to search for: clone-path traversal (`app/src/lib/git/clone.ts`, `sanitizeCloneName` in `app/src/lib/remote-parsing.ts`), deep-link filepath escape (`resolveWithin` guard in `app/src/ui/dispatcher/dispatcher.ts`), IPC sender validation (`app/src/main-process/trusted-ipc-sender.ts`), and account/hostname matching for credential use (`app/src/lib/find-account.ts`, `app/src/lib/repository-matching.ts`). None of these show the "trust an unverified caller-declared value as ground truth" pattern that the ZKPay report hinges on.

The one place where a comparable trust gap exists is in how partial-commit line selections are re-applied against a freshly recomputed diff, rather than the diff that was actually shown to the user when they made the selection.

### Title
Stale line-selection indices applied to a freshly recomputed diff can silently stage unintended content - (File: app/src/lib/git/apply.ts)

### Summary
`applyPatchToIndex` builds the patch used for partial commits by re-fetching the working directory diff at apply time and then reusing the `DiffSelection` (a set of absolute line indices) that was computed against whatever diff was rendered earlier in the UI. `formatPatch` trusts `file.selection.isSelected(absoluteIndex)` as ground truth for "the user chose to include this hunk line," without re-validating that the line at that index still represents the same logical change it did when the selection was made.

### Finding Description
`applyPatchToIndex` fetches a new diff via `getWorkingDirectoryDiff(repository, file)` and then calls `formatPatch(file, diff)`, which walks `diff.hunks` and, for each line, asks `file.selection.isSelected(absoluteIndex)` [1](#0-0) [2](#0-1) . The `DiffSelection` object only stores integer line indices, not any content hash or identity tied to the original diff snapshot the user reviewed. If the on-disk content of the file changes between the moment the user made their line selection in the Changes view and the moment `stageFiles`/`applyPatchToIndex` runs (e.g., because a crafted `.gitattributes` clean/smudge filter, LFS process, or other repository-defined tooling from a cloned/fetched attacker-controlled repository rewrites the file in the background), the hunk structure of the newly fetched diff can differ from the one the indices were computed against. `formatPatch` has no mechanism to detect this drift — it blindly maps old indices onto the new hunk lines and constructs a syntactically valid unified diff, which `git apply --cached` will happily accept, since git only validates the patch is internally consistent, not that it matches the user's original intent.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": the user reviews and approves diff A, but the actual staged/committed content is derived from diff B with A's line-selection bitmap applied to it, with no error and no user-visible warning. Because `formatPatch` throws only when the resulting patch is completely empty [3](#0-2) , most drift scenarios succeed silently rather than failing loudly.

### Likelihood Explanation
This requires an attacker-controlled repository to trigger a file-content change in the narrow window between diff render and commit apply (e.g., through a checked-in filter/hook mechanism), which is a real but timing-dependent condition rather than a trivially reliable one-shot exploit, so likelihood is moderate rather than high.

### Recommendation
Before calling `formatPatch` in `applyPatchToIndex`, compare the newly fetched diff's hunk structure/content against the diff the `DiffSelection` was computed from (e.g., by storing a content fingerprint of the diff alongside the selection state), and abort the partial-commit operation with a user-facing error if they no longer match, rather than silently re-mapping indices onto different content.

### Proof of Concept
1. Clone an attacker-controlled repository containing a modified file plus a mechanism that mutates the file shortly after checkout/status refresh (e.g., a scheduled/background process the repo's tooling triggers via `.gitattributes` filters).
2. In GitHub Desktop's Changes view, open the diff for that file and select specific lines/hunks to stage for commit.
3. Before clicking "Commit," let the attacker-controlled process alter the file so the working-directory diff's hunk boundaries shift.
4. Click "Commit." `applyPatchToIndex` re-fetches the diff (now different) and reuses the stale `DiffSelection` indices in `formatPatch`, producing a patch that stages different lines than what the user reviewed and approved, with no warning shown.

### Citations

**File:** app/src/lib/git/apply.ts (L52-62)
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

**File:** app/src/lib/patch-formatter.ts (L222-227)
```typescript
  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
  }
```
