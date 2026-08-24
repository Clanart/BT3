## Title
Attacker-crafted files containing literal conflict-marker-like text cause GitHub Desktop's Copilot conflict resolver to silently rewrite unrelated content — (File: `app/src/lib/copilot-conflict-context.ts`)

### Summary
The vulnerability class in the source report is: an operation accepts a degenerate/attacker-shaped input, passes invariant checks meant only for the legitimate case, and silently corrupts shared state (`checkpoint.count`) at the expense of another party, because the code conflates "this input looks like the expected shape" with "this input is actually what it claims to be."

The Desktop analog is in the Copilot-powered merge-conflict resolver. `extractConflictHunks` in [1](#0-0)  naively scans the *entire* file for any line matching a 7-character `<<<<<<<` / `=======` / `>>>>>>>` pattern and treats every match as a real Git conflict marker block, with no verification that the block was actually inserted by Git's merge machinery (e.g. via `MERGE_HEAD` state, `git diff --check`, or matching the file's actual unmerged blob content in the index). `reassembleResolvedFile` in [2](#0-1)  performs the exact same naive text scan when splicing the model's resolution back into the file on disk.

### Finding Description
1. Files eligible for Copilot resolution are only gated on `AppFileStatusKind.Conflicted`, i.e. any file Git reports as unmerged — see how `buildConflictContext` is invoked with such file lists in [3](#0-2) .
2. Once a file is "conflicted", the whole file (not just the actual merge-inserted regions) is scanned line-by-line for the fixed pattern `/^<{7}(?:\s|$)/`, `/^={7}$/`, `/^>{7}(?:\s|$)/` (`oursMarker`, `separatorMarker`, `theirsMarker`) at [4](#0-3) . Any line elsewhere in the file that happens to match this pattern — for example literal text in documentation, a test fixture, a string constant, or content deliberately placed there by whoever authored the conflicting branch — is treated as the start of a real conflict hunk and its surrounding text is captured as `oursContent`/`theirsContent` and sent to the Copilot model as if it were an actual side of the merge, per the loop in [5](#0-4) .
3. `getHunkSkipReason` and `validateResolutionPaths` only bound size and check that the number of returned hunks equals the number of "hunks" that `extractConflictHunks` found — they never validate that a detected hunk corresponds to a genuine unmerged blob region (see [6](#0-5) ). Since both the extraction and reassembly steps run the identical naive scan, the counts always line up, so this "invariant" passes even for fabricated marker blocks.
4. `reassembleResolvedFile` then walks the raw on-disk content with the same marker regexes and unconditionally splices the model's resolved text over whatever matched, at [7](#0-6) .
5. The reassembled content is written straight to disk and staged via `git add` in the app-store flow, with only a repository-path traversal check (`resolveWithin`) — no check that the replaced region was an actual Git conflict: [8](#0-7) .

The exact corrupted value is the *content of the reassembled file staged for commit* (`resolution.resolvedContent` written via `writeFile` in `app-store.ts`): it can include AI-hallucinated or attacker-steered text in place of content that was never part of the real merge conflict, and this happens silently — the resolution UI presents it as "resolved conflicts" with no indication that some of the "conflicts" processed were not real Git-inserted markers.

### Impact Explanation
This matches the "silent corruption of what user commits or pushes" impact category: a user merging/rebasing/cherry-picking a branch that an attacker contributed to (a PR branch, a fork, or any git remote content the user pulls) can have unrelated, non-conflicting file regions silently rewritten by the Copilot resolver and committed without the user reviewing that specific region as a real conflict. Because the fabricated "hunk" never contained genuine `<<<<<<<`/`=======`/`>>>>>>>` Git markers introduced by the merge, a user reviewing the diff for legitimate conflicts (as flagged by `git status`) can be misled about what was actually in dispute, and the model's replacement text — which the attacker can partially influence by choosing what text surrounds the fake markers and what the "ours"/"theirs" sides contain — ends up in the committed history.

### Likelihood Explanation
Likelihood is moderate: it requires (a) the user to have the Copilot conflict-resolution feature enabled, (b) an actual file-level merge conflict to exist somewhere in the same file for the file to be classified `Conflicted` by Git status, and (c) the attacker-controlled side of the merge to contain literal 7-character marker-like sequences elsewhere in that same file. This is a plausible scenario in any repo containing documentation/tests about Git conflict resolution, generated diff/patch fixtures, or any file where an attacker purposefully embeds marker-like strings knowing the target uses AI-assisted conflict resolution — no local access, credentials, or unnatural user action is required beyond a normal merge/rebase/cherry-pick that would occur in ordinary collaboration.

### Recommendation
- Restrict `extractConflictHunks` (and `reassembleResolvedFile`'s scan) to only the byte ranges that Git's index/`--diff3`/`ls-files -u` actually reports as unmerged for that path, rather than scanning the raw file text for marker-like patterns.
- Cross-validate any detected "hunk" against `git diff --check` or the unmerged stage blobs (`:1:path`, `:2:path`, `:3:path`) before sending content to the model or splicing a resolution back in.
- If a marker-like line is found outside a verified unmerged region, treat it as ordinary file content (as already done for malformed marker blocks) instead of a resolvable hunk.

### Proof of Concept
1. Create a repository where `docs/example.md` on `main` contains a legitimate discussion of merge conflicts with the literal lines:
   ```
   <<<<<<< snippet
   old text
   =======
   new text
   >>>>>>> snippet
   ```
   (fully valid content, no active Git conflict here).
2. On a feature branch, make an unrelated real edit elsewhere in `docs/example.md` that conflicts with a concurrent edit on `main` to the same file (so Git marks the file `Conflicted` in status, inserting its own real markers at a different location in the file).
3. Merge the feature branch into `main` in Desktop and invoke the Copilot conflict-resolution feature.
4. Observe that `extractConflictHunks` reports two hunks for `docs/example.md` — the real Git conflict and the attacker's literal marker text — and that after resolution, `reassembleResolvedFile` overwrites the "snippet" example content with AI-generated text, which then gets staged via `git add` and can be committed without ever being flagged to the user as a rewritten *non-conflict* region.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-context.ts (L179-243)
```typescript
export function extractConflictHunks(
  fileContent: string,
  contextLines: number = 3
): ReadonlyArray<IConflictHunk> {
  const lines = fileContent.split(/\r?\n/)
  const hunks: Array<IConflictHunk> = []

  let i = 0
  while (i < lines.length) {
    if (!oursMarker.test(lines[i])) {
      i++
      continue
    }

    const oursStart = i + 1
    const oursLines: Array<string> = []
    const baseLines: Array<string> = []
    let hasBase = false
    const theirsLines: Array<string> = []
    let hunkEnd = -1

    i = oursStart
    // Collect ours content
    while (i < lines.length) {
      if (baseMarker.test(lines[i])) {
        hasBase = true
        i++
        break
      }
      if (separatorMarker.test(lines[i])) {
        i++
        break
      }
      oursLines.push(lines[i])
      i++
    }

    // If diff3, collect base content until separator
    if (hasBase) {
      while (i < lines.length) {
        if (separatorMarker.test(lines[i])) {
          i++
          break
        }
        baseLines.push(lines[i])
        i++
      }
    }

    // Collect theirs content until closing marker
    while (i < lines.length) {
      if (theirsMarker.test(lines[i])) {
        hunkEnd = i
        i++
        break
      }
      theirsLines.push(lines[i])
      i++
    }

    // If we never found the closing marker, skip this malformed hunk
    if (hunkEnd === -1) {
      continue
    }

```

**File:** app/src/lib/copilot-conflict-context.ts (L367-408)
```typescript
export async function buildConflictContext(
  ourLabel: string,
  theirLabel: string,
  workingDirectory: string,
  files: ReadonlyArray<{
    readonly path: string
    /** Which side deleted the file (for delete-vs-modify conflicts). */
    readonly deletedSide?: 'ours' | 'theirs'
  }>
): Promise<ICopilotConflictContext> {
  const results = await Promise.all(
    files.map(async (file): Promise<IFileConflictContext> => {
      // Delete-vs-modify conflicts have no text markers on disk. Include
      // them in the context with metadata so the model can recommend
      // keep or delete — no file content is needed.
      if (file.deletedSide !== undefined) {
        return {
          path: file.path,
          hunks: [],
          deleteConflict: { deletedSide: file.deletedSide },
        }
      }

      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }

```

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-521)
```typescript
export function validateResolutionPaths(
  resolutions: ReadonlyArray<IRawFileResolution>,
  expectedFiles: ReadonlyArray<IFileConflictContext>
): void {
  const expectedPaths = new Set(expectedFiles.map(f => f.path))
  const expectedHunkCounts = new Map(
    expectedFiles.map(f => [f.path, f.hunks.length])
  )
  const returnedPaths = new Set(resolutions.map(r => r.path))

  for (const path of returnedPaths) {
    if (!expectedPaths.has(path)) {
      throw new CopilotValidationError(
        `Copilot returned resolution for unexpected file: ${path}`
      )
    }
  }

  if (returnedPaths.size !== resolutions.length) {
    throw new CopilotValidationError(
      'Copilot returned duplicate file paths in resolutions'
    )
  }

  const missingPaths: Array<string> = []
  for (const path of expectedPaths) {
    if (!returnedPaths.has(path)) {
      missingPaths.push(path)
    }
  }
  if (missingPaths.length > 0) {
    throw new CopilotValidationError(
      `Copilot did not return resolutions for: ${missingPaths.join(', ')}`
    )
  }

  for (const resolution of resolutions) {
    // Delete-vs-modify resolutions use action instead of hunks — skip count check
    if (resolution.action !== undefined) {
      continue
    }
    const expectedCount = expectedHunkCounts.get(resolution.path) ?? 0
    if (resolution.hunks.length !== expectedCount) {
      throw new CopilotValidationError(
        `Copilot returned ${resolution.hunks.length} hunk(s) for "${resolution.path}" but expected ${expectedCount}`
      )
    }
  }
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L549-599)
```typescript
export function reassembleResolvedFile(
  rawContent: string,
  hunkResolutions: ReadonlyArray<IHunkResolution>
): string {
  const eol = rawContent.includes('\r\n') ? '\r\n' : '\n'
  const lines = rawContent.split(/\r?\n/)
  const resultLines: Array<string> = []
  let hunkIndex = 0
  let i = 0

  while (i < lines.length) {
    if (reassemblyOursMarker.test(lines[i])) {
      // Look ahead to verify this is a well-formed conflict block:
      // must have a ======= separator and a >>>>>>> closing marker.
      let hasSeparator = false
      let closingIndex = -1
      for (let j = i + 1; j < lines.length; j++) {
        if (reassemblySeparatorMarker.test(lines[j])) {
          hasSeparator = true
        } else if (reassemblyTheirsMarker.test(lines[j])) {
          closingIndex = j
          break
        }
      }

      if (!hasSeparator || closingIndex === -1) {
        // Malformed marker — copy through as regular content
        resultLines.push(lines[i])
        i++
        continue
      }

      // Skip through the entire conflict marker block
      i = closingIndex + 1

      // Splice in the resolved content for this hunk
      if (hunkIndex < hunkResolutions.length) {
        const resolved = hunkResolutions[hunkIndex].resolvedContent
        if (resolved.length > 0) {
          resultLines.push(...resolved.split(/\r?\n/))
        }
      }
      hunkIndex++
    } else {
      resultLines.push(lines[i])
      i++
    }
  }

  return resultLines.join(eol)
}
```

**File:** app/src/lib/stores/app-store.ts (L7233-7267)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }

      // If the user resolved this file externally (e.g. in their editor) while
      // the result dialog was open, git status will report it with no remaining
      // conflict markers. Overwriting it with Copilot's stored content would
      // silently clobber their work, so skip it and let their resolution stand.
      // This mirrors how the manual conflicts dialog determines a file is
      // resolved (`hasUnresolvedConflicts`).
      const onDiskFile = state.changesState.workingDirectory.files.find(
        f => f.path === resolution.path
      )
      if (
        onDiskFile !== undefined &&
        isConflictedFileStatus(onDiskFile.status) &&
        !hasUnresolvedConflicts(onDiskFile.status)
      ) {
        continue
      }

      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```
