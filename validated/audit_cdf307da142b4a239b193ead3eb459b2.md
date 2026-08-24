### Title
Stale-Diff Line-Index Reuse in Partial Commit Staging Allows Silent Corruption of Committed Content - (File: `app/src/lib/git/apply.ts`)

### Summary
`Leap.depositEth()`'s bug is a classic check-then-act race: a value is validated against a snapshot (`totalRaised`) that an attacker can invalidate between the check and the actual state mutation, corrupting the intended outcome (blocking `maxRaiseAmount`). The same broken-invariant pattern exists in GitHub Desktop's partial-commit staging path: the user's line selection is validated/created against one diff snapshot, but it is *applied* against a second, independently re-fetched diff snapshot taken later, with nothing tying the two together except positional line indices.

### Finding Description
When a user partially stages a file (selects only some lines/hunks of a modified file for commit), the selection is recorded as an abstract, index-based `DiffSelection` object — a bitset of "selected" line indices relative to a diff that was rendered in the Changes view at some earlier time `T1`.

At commit time, `createCommit` → `stageFiles` → `applyPatchToIndex` is invoked [1](#0-0) . For any file with a partial selection, `stageFiles` calls `applyPatchToIndex` [2](#0-1) , which **re-fetches the diff from the current working directory at time `T2`**:

```
const diff = await getWorkingDirectoryDiff(repository, file)
...
const patch = await formatPatch(file, diff)
``` [3](#0-2) 

`formatPatch` then reapplies the user's line-index selection blindly onto this fresh diff, using only positional arithmetic — `absoluteIndex = hunk.unifiedDiffStart + lineIndex` and `file.selection.isSelected(absoluteIndex)` — with no check that the diff content at those indices is the same content the user actually saw and approved: [4](#0-3) 

There is no hash, checksum, or content comparison between the diff that produced the selection and the diff `applyPatchToIndex` fetches at staging time. If the tracked file's content on disk changes between `T1` (when the user reviewed the diff and picked lines) and `T2` (when `stageFiles`/`applyPatchToIndex` runs), the "selected" indices silently point at whatever hunks/lines now occupy those positions in the new diff — not the lines the user actually reviewed.

This mirrors the report's broken invariant exactly: a decision (`maxAllocation`/line selection) is made against a stale read of shared/mutable state (`totalRaised`/working-directory content), and nothing prevents that state from changing before the decision is executed.

### Impact Explanation
An attacker who controls a cloned/fetched repository can ship tooling that legitimately runs in the background while the repository is open in Desktop — e.g. a `package.json` `postinstall`/watch script, a build tool invoked through an editor task, or a git smudge/clean filter — that rewrites a tracked file shortly after the user opens its diff. If this happens in the window between the user selecting specific lines to commit and clicking "Commit", the attacker-modified content can be silently included in the commit (and subsequently pushed) even though the user never saw or approved it in the diff view, or conversely the user's intended change can be dropped. This is a silent corruption of what the user commits and pushes — the app gives no indication that the diff underlying the commit differs from the diff the user reviewed.

### Likelihood Explanation
Requires only unprivileged conditions already in scope: an attacker-controlled cloned/fetched repository whose companion tooling (not Desktop itself, not the user) modifies a tracked file during the small window between diff review and commit action. No admin rights, no local malware pre-installed by the attacker, no leaked credentials — the "malicious" process originates from the repository content itself and is a normal side effect of opening/building a project, which is a realistic workflow in Desktop.

### Recommendation
Before applying a partial-selection patch in `applyPatchToIndex`, verify that the diff fetched at staging time (`T2`) matches the diff the selection was computed against (`T1`) — e.g., by storing a hash/fingerprint of the diff (or file mtime/size/blob-oid) alongside the `DiffSelection`, and refusing to stage (or re-prompting the user to re-review) if the working tree has changed since the diff was captured.

### Proof of Concept
1. Open a repository in Desktop and modify `file.txt` with two independent hunks.
2. In the Changes diff view, deselect hunk 2, leaving only hunk 1 selected for commit (`DiffSelection` built against diff snapshot `T1`).
3. Before clicking "Commit," a background process from the repo (e.g., a running `npm run watch` task or a smudge filter triggered by another git operation) rewrites `file.txt` so that the new diff's hunk layout shifts (e.g., inserts lines above hunk 1), causing what were hunk-2's line positions to now correspond to different content.
4. Click "Commit." `applyPatchToIndex` fetches the new diff (`T2`) and `formatPatch` applies the stale selection's line indices to it [3](#0-2) [4](#0-3) .
5. The resulting commit contains content the user never reviewed or explicitly selected, with no warning shown by the app.

### Citations

**File:** app/src/lib/git/commit.ts (L26-31)
```typescript
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
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
