### Title
Conflict-marker rescanning during Copilot resolution reassembly can desynchronize from the original hunk boundaries, silently corrupting committed file content - (`app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The external report's core defect is that an iterative approximation can stop before it actually converges, yet the caller still treats the partial/inaccurate result as final and correct because only a coarse "did it finish" check is performed, not a check that the result is actually consistent with the true target. The same class of bug exists in GitHub Desktop's AI conflict-resolution pipeline: two independent, heuristic line scanners are each used to locate conflict-marker boundaries in the same file — one when building the prompt/expected hunk count, and a second, separate one when splicing the model's per-hunk output back into the file. The two scanners are only cross-checked by *counting* hunks, never by verifying they identify the *same* line ranges. Crafted marker-like text inside a hunk's own content can make the two scanners disagree on boundaries while still agreeing on count, so validation passes and the wrong span of the file is spliced — corrupting what gets written to disk and ultimately committed, with no error surfaced to the user.

### Finding Description
Conflict hunks are first extracted for the prompt in `extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts`, which walks the file line-by-line using `oursMarker`, `baseMarker`, `separatorMarker`, and `theirsMarker` to build each `IConflictHunk` and to compute `expectedHunkCounts` used later for validation. [1](#0-0) 

Later, after the model responds, `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` re-scans the *same raw file content* from scratch, independently, using its own marker regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) to find where to splice each hunk's resolved content: [2](#0-1) 

The well-formedness lookahead in `reassembleResolvedFile` searches forward for the *first* line matching the separator pattern and then the *first* line matching the closing marker pattern after that, with no awareness of whether those lines are genuinely the top-level markers or merely marker-like text that is part of the ours/theirs content itself (e.g. a file that legitimately contains example conflict-marker text, generated docs, or a crafted string literal): [3](#0-2) 

The only cross-check between the extraction pass and the reassembly pass is `validateResolutionPaths`, which compares the *count* of hunks returned by the model against `expectedHunkCounts` computed during extraction — it never verifies that the reassembly scan's line boundaries match the extraction scan's boundaries: [4](#0-3) 

If a conflicted file's ours/theirs content contains a marker-like line before the true closing `>>>>>>>` (e.g., embedded example conflict-marker text placed there by the "theirs" side, which is attacker-controlled content coming from a fetched branch/PR being merged), `reassembleResolvedFile`'s lookahead can lock onto that earlier line as `closingIndex`. The hunk count found by both scanners can still match (the file may still contain the same total number of well-formed-looking marker sequences), so `validateResolutionPaths` reports no discrepancy — but the actual text spliced in by `reassembleResolvedFile` no longer corresponds to the text region the model actually resolved. This is the direct analog of "the approximation finished but the final value is treated as correct even though it's inaccurate": the guard only checks *that a result was produced with the expected shape*, not that it is *positionally consistent with the true structure of the file*.

### Impact Explanation
Because `reassembleResolvedFile`'s output becomes the file content written to disk and then committed/pushed by the user via the normal Copilot conflict-resolution flow, a desync silently corrupts what the user commits: unrelated code between the fake and real closing marker can be dropped, and subsequent legitimate hunk boundaries in the same file shift, misapplying the model's per-hunk resolutions to the wrong regions of the file. This falls squarely under "silent corruption of what the user commits or pushes" — the user sees a seemingly successful Copilot resolution and commits it without conflict markers, unaware that unrelated code was deleted or that hunk content landed in the wrong place. The attacker-controlled surface is the fetched/merged branch's file content itself (the "theirs" side), which the report's scope explicitly allows (attacker controls a fetched repository's contents).

### Likelihood Explanation
This requires a specifically crafted conflicting file (e.g. a branch contributed by an attacker that is later merged, rebased, or cherry-picked by the victim) containing marker-like text inside a conflict hunk's own ours/theirs region — a plausible but non-trivial precondition. It does not require any privilege escalation, local access, or social engineering beyond a normal collaborative merge/rebase where the attacker controls one side's file content, which is a realistic path for supply-chain-style contributions (e.g. PR branches). No existing check in `validateResolutionPaths` or elsewhere detects the boundary desync, since it only performs a count comparison rather than a structural/offset comparison, so nothing currently stops this path.

### Recommendation
Make `reassembleResolvedFile` reuse the exact line offsets computed once by `extractConflictHunks` (e.g. by threading through hunk start/end line indices in `IConflictHunk`) instead of independently re-deriving marker boundaries from raw text a second time. If that isn't feasible, add a structural consistency check that verifies the boundaries found during reassembly match the same regions identified during extraction (not just hunk counts), and hard-fail (revert to showing the file as unresolved / surface an error) rather than silently proceeding when the two scans disagree — mirroring the report's recommended mitigation of reverting when convergence/consistency cannot be verified.

### Proof of Concept
1. Set up a merge/rebase where the "theirs" side content of a conflicted file includes, inside its own hunk content, a line that matches the closing-marker pattern before the genuine closing marker, e.g.:
```
<<<<<<< HEAD
real ours code
=======
some text that includes a literal line: >>>>>>> fake
more theirs code that should be kept
>>>>>>> branch-b
```
2. `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-242`) treats this as one hunk (it only stops the theirs-collection loop at the true `theirsMarker`), producing `expectedHunkCounts = 1` and hunk content including all of "some text... more theirs code that should be kept".
3. When Copilot returns exactly one resolved hunk, `validateResolutionPaths` passes (count matches).
4. `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) re-scans the same raw content; its lookahead finds `=======` then the *first* line matching the theirs-marker pattern — the embedded `>>>>>>> fake` line — and treats the block as ending there, splicing the model's resolved content in place of only part of the true hunk, then resuming line-by-line copy from `more theirs code that should be kept` onward as if it were ordinary post-hunk file content.
5. The resulting file is written and can be committed/pushed with content that differs from both the model's intended resolution and the pre-conflict source, with no error or warning shown to the user.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L179-242)
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
