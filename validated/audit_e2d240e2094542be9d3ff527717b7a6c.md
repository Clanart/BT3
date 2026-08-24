### Title
Stale line-index diff selection is silently re-applied to a freshly-fetched diff, corrupting partial commits - (File: `app/src/lib/stores/updates/changes-state.ts`, `app/src/lib/git/apply.ts`, `app/src/lib/patch-formatter.ts`)

### Summary
Like the `GovernanceHYBR::deposit` bug (a value computed from state *before* a mutating step is used against state measured *after* the mutation), GitHub Desktop computes a user's line-level commit selection against one diff snapshot, but at staging/commit time re-fetches a brand-new diff and blindly re-applies the old, index-based selection to it. If the tracked file's content changes between the two diff computations — which a malicious repository can trigger via git attribute-driven filters or Desktop's own background status polling — the wrong lines get staged and committed, without any error surfaced to the user.

### Finding Description
`WorkingDirectoryFileChange.selection` (`DiffSelection`) records inclusion/exclusion state as **absolute line indices into a specific diff's hunks**, not as content-addressed positions. `updateChangedFiles` in `app/src/lib/stores/updates/changes-state.ts` runs on every background status refresh and simply carries the old selection forward onto the new file object: [1](#0-0) 

Note it only discards the *rendered diff* if the selection state changed shape (line 90-95), but the `DiffSelection` line-index object itself is preserved verbatim via `withSelection`, regardless of whether the on-disk file content — and therefore the real hunk layout — has changed since that selection was made.

At commit time, `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts`) does not reuse the diff the UI displayed to the user. It fetches a **new** diff right before formatting the patch: [2](#0-1) 

`formatPatch` (`app/src/lib/patch-formatter.ts`) then walks this new diff's hunks and decides which lines to keep purely via `file.selection.isSelected(absoluteIndex)`: [3](#0-2) 

If the file changed between the moment the user selected lines in the UI and the moment `applyPatchToIndex` re-diffs the file, the hunk boundaries/line counts shift, so the same absolute indices now refer to different lines. `formatPatch` will silently produce a patch that stages/discards different content than what the user visually reviewed and approved, and `git apply --cached` will accept it as a syntactically valid patch — no error, no warning.

An attacker who controls a cloned/fetched repository (its tracked content or `.gitattributes`) can influence this without any local access, admin rights, or pre-existing malware: e.g., a `clean`/`smudge` filter, `.gitattributes`-driven `ident`/keyword-expansion, or any config that causes git to report varying file content across successive `git diff`/`git status` invocations. Desktop's own architecture invokes `git status`/`git diff` repeatedly in the background (the mechanism `updateChangedFiles` exists to reconcile), so the window between "selection is made against diff A" and "patch is generated against diff B" is a normal, frequently-occurring code path, not a contrived race.

### Impact Explanation
This breaks the core commit-selection invariant: the file lines the user visually approved for inclusion in a commit are not necessarily the lines actually staged and pushed. This is a "silent corruption of what the user commits or pushes" — the exact class of impact called out as in-scope. Depending on the corrupted hunk mapping, this could exclude security-relevant lines the user intended to commit, or include lines/content the user explicitly deselected (e.g., secrets, debug code, or attacker-injected content hidden via a filter), all without any indication in the UI or a git error.

### Likelihood Explanation
Requires: (1) a partial/line-level selection on a file, and (2) the working-tree diff for that file to change between UI selection and the commit's re-diff — both of which are enabled purely by content in an untrusted/malicious repository (filters, attributes, or any external process the repo itself is set up to trigger) and Desktop's normal background re-diffing cadence. No elevated privileges, physical access, or pre-existing host compromise are needed; the trigger surface is entirely repo-content-driven, matching the required attacker model.

### Recommendation
Do not reuse line-index-based `DiffSelection` across diff recomputations for a file whose content/hash has changed. Either:
- Invalidate (reset to `None`/`All`) a file's partial selection whenever the underlying diff for that file changes (compare against the diff's content hash or hunk signature, not just file identity), or
- Have `applyPatchToIndex`/`formatPatch` operate against the exact same diff object the user's selection was computed from (fail/re-prompt if it has gone stale) rather than silently re-fetching and reapplying stale indices.

### Proof of Concept
Conceptual repro (cannot be executed here, but follows directly from the code paths cited above):
1. Clone a repository containing a tracked file `f` with a `.gitattributes` `clean` filter (or any mechanism) that normalizes/rewrites `f`'s content differently across two successive `git diff` invocations (e.g., filter output depends on external state/time).
2. In Desktop, open the Changes view; select a partial subset of lines in `f` for commit — this establishes a `DiffSelection` with absolute line indices bound to diff snapshot A.
3. Let Desktop's periodic background status refresh run `updateChangedFiles`, which reapplies the same `DiffSelection` object to the (possibly re-ordered) new `WorkingDirectoryFileChange` for `f` (`changes-state.ts:44-59`).
4. Click "Commit". `applyPatchToIndex` re-diffs `f` (`apply.ts:60`), producing diff snapshot B with a different hunk layout because the filter output changed.
5. `formatPatch` applies the stale selection's indices to diff B's hunks (`patch-formatter.ts:143-171`), staging/discarding different lines than what was shown and selected in the UI — the resulting commit silently differs from user intent.

### Citations

**File:** app/src/lib/stores/updates/changes-state.ts (L44-59)
```typescript
    .map(file => {
      const existingFile = filesByID.get(file.id)
      if (existingFile) {
        if (clearPartialState) {
          if (
            existingFile.selection.getSelectionType() ===
            DiffSelectionType.Partial
          ) {
            return file.withIncludeAll(false)
          }
        }

        return file.withSelection(existingFile.selection)
      } else {
        return file
      }
```

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
