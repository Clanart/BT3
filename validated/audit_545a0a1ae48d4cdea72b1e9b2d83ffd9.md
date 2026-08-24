## Analysis

The Sherlock finding's broken invariant is: a caller-supplied "quantity" (`msg.value`) is checked with `>=` instead of `==`, so a **excess amount is silently absorbed instead of being accounted for/refunded**, and nothing downstream flags the mismatch.

The closest verifiable analog in this codebase is in the Copilot AI conflict-resolution pipeline, where two independent parsers count "conflict marker blocks" in the same file, and a length mismatch between them causes the reassembly step to **silently discard (not preserve, not error on) whole conflict blocks** before the file is written to disk and staged with `git add`.

### Title
Copilot conflict-resolution reassembly silently deletes unresolved conflict blocks on hunk-count mismatch - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
`extractConflictHunks` (used to build the prompt sent to the model and the "expected" hunk count for validation) and `reassembleResolvedFile` (used to splice the model's per-hunk answers back into the on-disk file) each independently re-parse the same `<<<<<<<`/`=======`/`>>>>>>>` marker syntax from the raw file content, but they diverge in how they handle a malformed/unterminated marker. When the two counts diverge, `reassembleResolvedFile` falls back to silently dropping an entire conflict block — both "ours" and "theirs" content — rather than preserving the original text or raising an error, and the result is written straight to disk and `git add`ed.

### Finding Description
`extractConflictHunks` scans a conflicted file line-by-line for marker sequences [1](#0-0) . If it starts collecting a hunk (`<<<<<<<` matched) but never finds a valid closing `>>>>>>>` before EOF, `hunkEnd` remains `-1` and the hunk is skipped — but critically, the inner collection loops have already advanced `i` all the way to `lines.length` [2](#0-1) , so the *outer* `while (i < lines.length)` loop terminates immediately. Any real, well-formed conflict that physically appears later in the file after such a malformed marker is **never extracted** and never counted in `expectedFiles[i].hunks.length`, which is what `validateResolutionPaths` uses as ground truth [3](#0-2) .

`reassembleResolvedFile`, however, re-parses the same raw content independently with its own marker regexes, and on a malformed/unclosed marker it only advances by **one line** and keeps scanning [4](#0-3) . So it will still find and attempt to splice a resolution into any well-formed conflict block that occurs after the point where `extractConflictHunks` gave up.

Because the model's returned `hunks` array length was validated against `extractConflictHunks`'s (potentially undercounted) hunk total, `hunkResolutions.length` can be smaller than the number of blocks `reassembleResolvedFile` actually walks through. When `hunkIndex` reaches or exceeds `hunkResolutions.length`, the code takes this path:

```
if (hunkIndex < hunkResolutions.length) {
  const resolved = hunkResolutions[hunkIndex].resolvedContent
  if (resolved.length > 0) resultLines.push(...resolved.split(/\r?\n/))
}
hunkIndex++
``` [5](#0-4) 

The marker block (`i = closingIndex + 1`, i.e. the `<<<<<<<`, `=======`, both sides' content, and `>>>>>>>`) has already been skipped over without being copied into `resultLines`, and since `hunkIndex < hunkResolutions.length` is `false`, **nothing is pushed to replace it**. The entire conflict block — content from both branches — is silently deleted from the final file content, with no thrown error and no fallback to preserving the original text.

That corrupted `resolvedContent` string is passed straight through `reassembleResolutions` [6](#0-5)  up to `_applyCopilotConflictResolutions`, which writes it directly to disk and stages it with no re-validation:

```
await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
pathsToStage.push(resolution.path)
...
await git(['add', '--', ...pathsToStage], repository.path, 'copilotConflictResolution')
``` [7](#0-6) 

### Impact Explanation
The attacker is whoever controls the branch/commit content being merged, rebased, or cherry-picked (a normal collaborator, or a compromised/malicious remote/fork the user pulls from). By shaping the content of a file so that it contains a line matching the 7-character conflict-marker pattern (`^<{7}(?:\s|$)`) without a corresponding valid closing sequence anywhere after it in the file — entirely plausible in ordinary text/documentation/fixture content that legitimately discusses or demonstrates Git conflict-marker syntax — the attacker can desynchronize `extractConflictHunks`'s hunk count from what `reassembleResolvedFile` will actually encounter. Any genuine, unrelated conflict located after that point in the same file is then silently erased (both sides' content, not just one) when the user accepts the Copilot-generated resolution, and the mutilated file is committed/pushed without any warning. This is a silent corruption of what the user commits, one of the explicitly valid impact classes, with no additional local access, credentials, or social engineering required beyond the user merging/rebasing the attacker-influenced branch and using the built-in "Resolve with Copilot" feature.

### Likelihood Explanation
The `_applyCopilotConflictResolutions` code shows an explicit awareness that clobbering user data is a real risk (it checks `hasUnresolvedConflicts` before overwriting a file a user already resolved externally) [8](#0-7) , but there is no cross-check between the hunk count `validateResolutionPaths` verified and the hunk count `reassembleResolvedFile` actually consumes — these are two independently maintained regex-based state machines over the same input with different failure semantics, and the code has no assertion that they produce the same block count. The `SinglePromptFileLimit`/batching logic makes multi-file, multi-conflict merges routine, increasing the chance that some file in a batch trips this divergence. No dialog, log, or error surfaces when this happens (the mismatch is treated as a normal "extra hunk resolutions" case in `reassembleResolvedFile`, not flagged), so it is silent by design of the current fallback.

### Recommendation
- Make `reassembleResolvedFile` throw a `CopilotValidationError` (instead of silently skipping) if it encounters more well-formed conflict blocks than there are entries in `hunkResolutions`, mirroring the `!==` strict-equality check already used in `validateResolutionPaths`.
- Better, use a single shared marker-parsing routine for both `extractConflictHunks` and `reassembleResolvedFile` so the two can never disagree on hunk boundaries/count.
- As a safety net, when `hunkIndex >= hunkResolutions.length` in `reassembleResolvedFile`, preserve the original marker block verbatim (fail-safe) rather than deleting it, and surface an error/warning to route the file back to manual conflict resolution.

### Proof of Concept
1. Attacker pushes a branch that modifies `docs/CONFLICTS.md` to add, among ordinary prose, a line consisting of exactly `<<<<<<< example` with no matching `=======`/`>>>>>>>` anywhere later in that file (e.g., as an illustrative but intentionally truncated example of Git conflict-marker syntax).
2. In the same repository/branch set, a genuine, unrelated conflict exists later in `docs/CONFLICTS.md` (or is introduced by the attacker's PR touching another section of the file that the user has also edited).
3. The user merges/rebases this branch in GitHub Desktop; Git produces on-disk conflict markers for the real conflict, plus the attacker's already-present fake marker line as plain content.
4. User clicks "Resolve with Copilot": `extractConflictHunks` walks the file, hits the fake `<<<<<<<` line, fails to find a closing `>>>>>>>` before EOF, and its outer scan exits at `lines.length` — the real conflict located after the fake marker is never extracted, so `expectedFiles` for this path under-reports its hunk count and the model is asked to resolve fewer hunks than actually exist.
5. `reassembleResolvedFile` independently re-walks the same raw file, still finds the real (well-formed) conflict block after the fake line, but `hunkIndex >= hunkResolutions.length` at that point, so the block is skipped with no replacement pushed — both sides of the real conflict vanish from the output.
6. The user clicks "Continue Merge"; `_applyCopilotConflictResolutions` writes the mutilated content to disk and runs `git add`, so the user commits/pushes a file silently missing the disputed code, with no error shown.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L182-214)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L228-242)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-579)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L581-591)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-642)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
}
```

**File:** app/src/lib/stores/app-store.ts (L7241-7256)
```typescript
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
```

**File:** app/src/lib/stores/app-store.ts (L7258-7267)
```typescript
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
