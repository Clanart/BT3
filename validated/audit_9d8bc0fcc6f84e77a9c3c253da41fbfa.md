### Title
Positional (not identity-based) hunk splicing in Copilot conflict resolution can silently misassign AI-merged content to the wrong conflict block - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` splices Copilot's per-hunk resolutions back into a conflicted file purely by **order of appearance**, matching the `hunkIndex`-th `<<<<<<<...=======...>>>>>>>` block found while re-scanning the raw file to the `hunkIndex`-th entry in `hunkResolutions`. There is no verification that the marker block currently being spliced corresponds to the same conflict the model was actually shown (e.g., no content hash, anchor text, or line-number check tying a resolution back to its source hunk). The only safety check, `validateResolutionPaths` (`app/src/lib/copilot-conflict-resolution.ts:473-521`), verifies the total *count* of hunks per file matches, not their identity or order alignment against the original extraction.

### Finding Description
The conflict-hunk extraction (`extractConflictHunks`, `app/src/lib/copilot-conflict-context.ts:182-279`) and the later reassembly (`reassembleResolvedFile`, `app/src/lib/copilot-conflict-resolution.ts:549-599`) are two **independent** line-by-line scans of the same raw file content, using near-identical but separately defined regexes for the conflict markers (`oursMarker`/`baseMarker`/`separatorMarker`/`theirsMarker` in one file vs. `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` in the other). Because the two scans are independently implemented, any content in the conflicted file that:
- is not itself a real git conflict marker but happens to match `^<{7}`, `^={7}`, or `^>{7}` (e.g., literal `<<<<<<<`/`=======`/`>>>>>>>` sequences embedded in a source file, a markdown doc about git conflicts, a diff/patch quoted in a comment, or a generated changelog), or
- differs subtly in how the two regex sets classify a "malformed" vs "well-formed" block (extraction has a `hunkEnd === -1` skip path; reassembly has a `hasSeparator`/`closingIndex` check with slightly different look-ahead logic),

can cause the two functions to disagree on how many real conflict blocks exist, or on the boundaries of a block. `reassembleResolvedFile` reconciles the mismatch **silently**: it just increments `hunkIndex` for whatever marker block it finds and splices `hunkResolutions[hunkIndex]` into it, with no assertion that this is the *same* conflict the model resolved. `validateResolutionPaths` only checks that `resolution.hunks.length === expectedFiles hunk count` from extraction — if both scans happen to agree on the *count* (even if they disagree on which lines constitute which hunk, e.g. due to decoy markers shifting hunk boundaries), no error is raised and the wrong resolved content is silently written into the wrong location of the file that the user then stages and commits (`app-store.ts:7258` `writeFile(absolutePath, resolution.resolvedContent, 'utf8')`, followed by `git add`).

This maps to the seed report's broken invariant: an edge case is not explicitly guarded (there, `collateralDecimals == lvlUsdDecimals`; here, "decoy marker text that is not a genuine conflict marker but still matches the regex, or a malformed marker interpreted differently by the two independent scanners") and the code silently proceeds with a value that should have triggered a protective branch/error, instead of failing safely.

### Impact Explanation
If the two scans misalign on hunk boundaries while agreeing on total count, the AI-resolved content for one conflict gets written into a different conflict's location. This is a silent corruption of what the user commits and pushes — the file the user believes was correctly merged by Copilot instead contains logic from a different hunk spliced into the wrong place, which is not obviously visible unless the user manually re-diffs against both parent branches. Because this flows into `git add` and the eventual commit/push, incorrect code (potentially reverting a security fix, reintroducing removed code, or dropping a colliding hunk's intended change) can be committed and pushed without the user noticing, since the merge/rebase/cherry-pick UI presents the "summary" and per-hunk `reasoning` as ground truth for what changed.

### Likelihood Explanation
This requires the repository content itself (attacker-controlled via a malicious branch/PR that gets merged/rebased/cherry-picked) to contain lines that coincidentally or deliberately match `^<{7}`, `^={7}(?:\s|$)?`, `^>{7}` patterns without being real conflict markers — for example a file documenting git conflict-resolution examples, a vendored diff/patch file, or specially crafted decoy text placed by an attacker anticipating this flow, combined with an actual conflict occurring in the same file during a merge. This is a plausible but non-trivial trigger: it needs both (a) attacker-controlled repository content with marker-like text and (b) the victim actually hitting a real conflict in that file and choosing to use Copilot's automatic conflict resolution. I could not fully verify the exact extraction-side regexes (`oursMarker`, `baseMarker`, etc. in `app/src/lib/copilot-conflict-context.ts`) beyond what was retrieved, since the file read for lines 49-182 could not be completed in this session — the precise regex definitions and whether they truly diverge from `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` should be confirmed by reading `app/src/lib/copilot-conflict-context.ts` directly.

### Recommendation
Do not rely on positional order alone to reconcile independently-scanned hunk boundaries. Either:
1. Have `extractConflictHunks` and `reassembleResolvedFile` share a single marker-scanning implementation/regex module so both interpret the same file identically, eliminating any chance of divergence, or
2. Tie each resolution to its source hunk with an explicit anchor (e.g., include a hash/index of the exact marker-block text sent to the model, and verify at splice time that the block found in `reassembleResolvedFile` matches the corresponding extracted hunk's `oursContent`/`theirsContent` before substituting), throwing `CopilotValidationError` on mismatch instead of silently proceeding.
3. Additionally validate hunk-by-hunk content association (not just aggregate count) in `validateResolutionPaths`.

### Proof of Concept
Conceptual PoC (not fully verified against `extractConflictHunks`'s exact regex source, which I was unable to re-read in this session):
1. Attacker contributes a branch containing a file, e.g. `merge-notes.md`, with a real conflict-triggering change plus an embedded decoy block of text that looks like `<<<<<<< some-example\n...\n=======\n...\n>>>>>>> other-example` (e.g., inside a fenced code sample explaining how to resolve conflicts), positioned in a way that shifts how many marker blocks the reassembly scan believes exist relative to the extraction scan, while both computed counts still match.
2. Victim merges/rebases/cherry-picks this branch in Desktop, hits an actual conflict in this file (and possibly others processed in the same batch, since resolutions are batched — see `SinglePromptFileLimit`), and uses "Resolve with Copilot".
3. `extractConflictHunks` builds N hunk contexts sent to the model; the model returns N `resolvedContent` entries in order.
4. `reassembleResolvedFile` re-scans the same raw file independently and, due to the decoy marker interacting differently with its look-ahead logic (`hasSeparator`/`closingIndex`), splices `hunkResolutions[k]` into a different marker block than the one extraction associated with hunk `k`.
5. `validateResolutionPaths` only checks `resolutions.hunks.length === expectedHunkCounts.get(path)`, which still matches, so no error is thrown.
6. The wrong merged content is written to disk via `writeFile` and staged via `git add` (`app-store.ts` `applyCopilotConflictResolutions`), and the user commits/pushes the file with silently corrupted content. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** app/src/lib/copilot-conflict-context.ts (L182-279)
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

    // The ours marker line is at oursStart - 1
    const markerStart = oursStart - 1
    const contextStart = Math.max(0, markerStart - contextLines)
    const contextEnd = Math.min(lines.length - 1, hunkEnd + contextLines)

    // Clamp context to not include conflict markers from adjacent hunks
    const contextBeforeLines: Array<string> = []
    for (let j = markerStart - 1; j >= contextStart; j--) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextBeforeLines.unshift(lines[j])
    }

    const contextAfterLines: Array<string> = []
    for (let j = hunkEnd + 1; j <= contextEnd; j++) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextAfterLines.push(lines[j])
    }

    const contextBefore = contextBeforeLines.join('\n')
    const contextAfter = contextAfterLines.join('\n')

    hunks.push({
      oursContent: oursLines.join('\n'),
      theirsContent: theirsLines.join('\n'),
      baseContent: hasBase ? baseLines.join('\n') : null,
      contextBefore,
      contextAfter,
    })
  }

  return hunks
}
```

**File:** app/src/lib/stores/app-store.ts (L7233-7268)
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
    }
```
