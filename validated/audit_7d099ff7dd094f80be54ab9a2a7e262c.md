### Title
Stale line-selection indices are silently reapplied against a freshly re-computed diff during partial commit/stage, causing corruption of committed content - ([File: app/src/lib/git/apply.ts])

### Summary
GitHub Desktop lets a user select individual lines/hunks of a file's diff to partially stage/commit. The selection is stored as a bitmap of *absolute line indices* computed against the diff object rendered in the UI. When the commit is actually created, Desktop does **not** reuse that diff — it re-runs `git diff` from scratch and blindly re-applies the old, index-based selection to the new diff's line positions. If the working-directory file changes between the time the user makes their selection and the time the commit executes, the indices no longer correspond to the same lines, so the wrong content is silently included/excluded from the commit — mirroring the report's core defect: a value ("ideal"/pre-transformation state) is used downstream instead of being re-validated against the post-transformation reality, producing silently wrong accounting (here, wrong commit content) with no error raised.

### Finding Description
The partial-stage path is `stageFiles` → `applyPatchToIndex`: [1](#0-0) 

`applyPatchToIndex` re-fetches the diff independently of whatever diff the UI used to build `file.selection`: [2](#0-1) 

`formatPatch` then walks the *freshly fetched* diff's hunks and lines, and decides whether each line is included by calling `file.selection.isSelected(absoluteIndex)`, where `absoluteIndex = hunk.unifiedDiffStart + lineIndex` is computed from the new diff's hunk layout, but `file.selection` is the bitmap the user built while looking at the old diff: [3](#0-2) 

There is no check anywhere in this path that the diff used to construct `file.selection` still matches the diff used to build the patch. The only guard in `formatPatch` is that the resulting patch must not be empty: [4](#0-3) 

That guard does not detect a shifted/misaligned selection — it only fires when literally nothing is selected. If hunk boundaries or line counts shift (e.g., a background process appends/removes/reorders lines, or line endings change — which Desktop explicitly detects and reports via `LineEndingsChange` from `git diff` stderr, but never uses to invalidate a stale selection): [5](#0-4) 
then `isSelected(absoluteIndex)` will silently answer against the *wrong* line, causing `formatPatch` to include lines the user never selected (or drop ones they did) without any error, warning, or diff re-render forcing the user to re-confirm.

### Impact Explanation
This breaks the fundamental guarantee that "what the user visually selected in the diff viewer is exactly what gets committed/pushed." A hostile or compromised cloned repository can ship tooling that is routinely auto-run in normal Desktop workflows (file watchers, format-on-save integrations, linters, `post-checkout`/build scripts) that mutate tracked files shortly after checkout. If such mutation happens between the user's line-level selection and the click on "Commit," the commit silently contains different content than what was displayed and approved — e.g., re-including a deselected malicious line, or excluding a security-relevant deletion — with git reporting a normal, successful commit. Since Desktop hashes/pushes this corrupted commit, the corruption propagates upstream to the remote/PR without the user's awareness, satisfying "silent corruption of what the user commits or pushes."

### Likelihood Explanation
Partial staging (selecting individual lines/hunks) is a common Desktop feature, and re-running `git diff` at commit-time (rather than caching/reusing the exact diff the selection was built from) is unconditional on every partial commit. Any file-system race — even a benign one caused by another process, editor auto-format, or a repo-shipped watch/build script — is sufficient; no privilege escalation or local malware pre-positioning is required, only that a change lands in the working tree in the window between selection and commit, a window whose length is entirely under the user's/tooling's control and not bounded by Desktop.

### Recommendation
Before applying `file.selection` to a newly-fetched diff in `applyPatchToIndex`/`formatPatch`, verify that the diff used to build the selection is still structurally consistent with the diff used at commit time (e.g., compare file content hash/mtime, or re-derive/re-validate the selection against the new hunk structure) and fail loudly (blocking the commit and forcing the user to re-review) rather than silently reapplying possibly-misaligned absolute indices.

### Proof of Concept
1. Open a repository in Desktop; modify a tracked file with several distinct hunks.
2. In the Changes view, partially select some lines of one hunk to stage/commit, leaving other lines (e.g., a suspicious added line) intentionally unselected.
3. Before clicking "Commit," have any background process shipped with the repo (a watcher, formatter, or a script triggered by opening the file) append/remove a line earlier in the file, shifting subsequent line offsets — this only changes line positions, not the semantic diff structure Desktop already displayed.
4. Click "Commit." `stageFiles` → `applyPatchToIndex` (`app/src/lib/git/apply.ts:60`) re-fetches the diff fresh, and `formatPatch` (`app/src/lib/patch-formatter.ts:143-171`) reapplies the stale `file.selection` bitmap against the new hunk offsets.
5. Inspect the created commit: it contains content different from what was shown/selected in the UI — the previously-deselected line may now be included (or a selected one dropped) — with no error surfaced to the user.

### Citations

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
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

**File:** app/src/lib/patch-formatter.ts (L222-227)
```typescript
  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
  }
```

**File:** app/src/lib/git/diff.ts (L398-400)
```typescript
  const lineEndingsChange = parseLineEndingsWarning(stderr)

  return buildDiff(stdout, repository, file, 'HEAD', 'HEAD', lineEndingsChange)
```
