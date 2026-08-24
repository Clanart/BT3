### Title
AI conflict-resolution write path splices resolved content by naive marker position, not identity, allowing attacker-crafted branch content to corrupt what gets committed - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
The external report's underlying pattern is: a function assumes a precondition holds ("balance is zero" / "this is the Nth call in a known order") but nothing in the code enforces that assumption, so state can silently diverge from what the caller believes, and because the operation runs in **batches**, a single bad match is very hard to spot. The GitHub Desktop analog is in the Copilot-assisted merge-conflict resolution feature: `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` splices the model's per-hunk resolutions into the on-disk file purely by **positional order of regex-matched marker blocks**, with no identity check (line numbers, content hashes, or timestamps) tying a resolution back to the specific conflict it was generated for.

### Finding Description
`reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) walks the raw on-disk file line by line and, for every line matching `^<{7}` that is later followed by a `^={7}$` and `^>{7}` line, treats it as a conflict block and replaces it with `hunkResolutions[hunkIndex]`, incrementing `hunkIndex` for every block encountered: [1](#0-0) 

`validateResolutionPaths` (`app/src/lib/copilot-conflict-resolution.ts:473-521`) only checks that the returned file paths match the expected set and that the **hunk count** per file matches what was gathered when the prompt was built: [2](#0-1) 

Nothing ties a specific `resolvedContent` entry to a specific conflict block other than "the Nth one we encountered." The invariant "conflict block N in the file the model saw is still conflict block N in the file we splice into" is assumed, not enforced — mirroring the FuelToken bug's assumed-but-unenforced ordering invariant.

`_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7169-7269`) then writes the reassembled content straight to disk and stages it: [3](#0-2) 

The only staleness guard present checks whether the file *still has unresolved conflict markers* according to the cached working-directory status (`hasUnresolvedConflicts`), to avoid clobbering a conflict the user resolved externally — it does **not** re-verify that the marker positions/order in the current on-disk file still match what was sent to the model: [4](#0-3) 

Because a repository whose content the attacker controls (a branch/PR the victim merges, rebases, or cherry-picks) can contain a file with lines that coincidentally match the exact marker regexes (`^<{7}`, `^={7}$`, `^>{7}` — e.g. documentation or fixtures that show literal example conflict markers, of the kind visible right in this very repo's own test fixtures), the population of "how many/which blocks are conflicts" that the reassembly regex finds on disk can diverge from what the earlier hunk-gathering step counted when it built the prompt (e.g. if the file is touched between context-gathering and the (potentially long-running, streamed, retried) model turn). When that happens, `reassembleResolvedFile` will silently splice a resolution generated for one conflict into a different, unrelated block, or drop/duplicate content, producing a file whose content the user did not actually approve. This is exactly the “batched operation whose per-item correctness depends on an unenforced ordering assumption, and where a mismatch is essentially undebuggable to the end user” pattern from the report — except here the corrupted value is the source file content that the user is about to `git add`/commit/push.

### Impact Explanation
If the positional mismatch occurs, the file that gets `writeFile`'d and `git add`'d (`app/src/lib/stores/app-store.ts:7258-7259`) is not the content the model actually reasoned about — it is a corrupted composite of unrelated hunks. This is committed and can be pushed without further diffing against the original per-hunk intent, satisfying the "silent corruption of what the user commits or pushes" impact class. Because the write happens directly to the working file that is then staged with `git add`, there is no independent verification step (e.g., a full-file diff review) enforced before staging.

### Likelihood Explanation
This requires: (1) the victim uses the AI merge-conflict-resolution feature, (2) a repository/branch under attacker influence contains content that confuses the naive line-anchored marker scan (either genuine marker-like text elsewhere in the file, or a race between context-gathering and the (multi-minute, streamed, retried) model turn during which the file is touched), and (3) `validateResolutionPaths`'s hunk-count check happens to still pass. This is a narrower, feature-specific condition rather than a universal exploit — I was not able to fully trace the original hunk-extraction function that builds `IFileConflictContext.hunks`/`rawContent` from the on-disk file (it lives outside the files surfaced by my searches), so I cannot confirm with certainty whether that code and `reassembleResolvedFile` are guaranteed to agree on marker boundaries in all cases, or whether there is already an implicit re-scan that would catch a mismatch. This is a real gap in my verification given the available tool budget.

### Recommendation
- Anchor each hunk resolution to something more specific than positional order — e.g., a content hash or verbatim echo of `oursContent`/`theirsContent` that `reassembleResolvedFile` can verify against the corresponding on-disk block before substitution, and abort/skip (falling back to manual resolution) on mismatch.
- Re-read the file and re-derive hunk boundaries immediately before the write in `_applyCopilotConflictResolutions`, and reject the resolution if the freshly detected marker positions/count differ from what was used to build the prompt.
- Surface a full pre/post diff of each file to the user before "Continue Merge" commits the AI-resolved content, rather than relying solely on the reasoning summary.

### Proof of Concept
Full end-to-end reproduction was not completed given the investigation scope; the concrete code-level evidence supporting the flaw is the pairing of:
1. Purely order-based splicing with no content/identity check: [5](#0-4) 
2. A validation step that only checks counts, not identity: [6](#0-5) 
3. An unconditional disk write of the reassembled content once the (stale, path-based) staleness check passes: [7](#0-6) 

A concrete, minimal repro scenario would be: create a branch whose "theirs" version of a conflicted file embeds literal lines `<<<<<<< foo` / `=======` / `>>>>>>> bar` inside a comment/docstring (not an actual git conflict marker), merge it so the file also gets one real git-inserted conflict block, and run "Resolve with Copilot." Because I could not access the original hunk-extraction code to confirm it and `reassembleResolvedFile` count conflict blocks identically in this scenario, I present this as a plausible but not fully verified proof of concept, and recommend a Devin session with full repository access to construct and run this fixture end-to-end to confirm exploitability before treating it as fully validated.

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-591)
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

**File:** app/src/lib/stores/app-store.ts (L7233-7259)
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
```
