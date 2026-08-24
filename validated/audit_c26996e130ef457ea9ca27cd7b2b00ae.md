### Title
Divergent conflict-marker parsers let a crafted merge conflict corrupt Copilot's reassembled file silently - (`app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
GitHub Desktop's Copilot conflict-resolution feature parses conflict markers **twice**, using two independently-written scanners that don't agree on what constitutes a well-formed conflict block. The first pass (`extractConflictHunks`) decides how many hunks exist and is used to build the model prompt and validate the model's response; the second pass (`reassembleResolvedFile`) walks the file again to splice the model's per-hunk output back into the original content. Because these two loops handle malformed/adjacent marker sequences differently, they can disagree about hunk boundaries for the same file — the same "two parallel data structures kept in sync only by loop position/order" pattern as the H-03 report, where a skip in one traversal is not mirrored in the other.

### Finding Description
`extractConflictHunks` in [1](#0-0)  finds an opening `<<<<<<<` marker and then scans forward for `=======`/`|||||||`, then continues scanning for the closing `>>>>>>>`. Critically, the "collect theirs" loop at [2](#0-1)  does not check for a *nested* `<<<<<<<`/`=======` while searching for the closing marker — it blindly appends every line as `theirsContent` until it hits the first `>>>>>>>` it encounters, even if that marker actually belongs to a second, later conflict block. If no closing marker is ever found, the loop consumes the rest of the file and the whole hunk is silently discarded (`continue` at line 241, but `i` is already at EOF).

`reassembleResolvedFile`, used later to splice the model's resolutions back into the on-disk content, re-implements the same scan independently at [3](#0-2) , using its own lookahead at [4](#0-3) . It only checks for `=======` and `>>>>>>>` while looking ahead — it never looks at `|||||||`, and its "malformed" fallback advances only one line (`i++; continue`) rather than consuming to EOF, so it can resynchronize and find hunks that `extractConflictHunks` already merged, split, or dropped.

Because the two functions are not the same code (no shared parser) and are invoked at different times — once during context gathering (`buildConflictContext`, [5](#0-4) ), and once when writing resolutions back to disk (`applyCopilotConflictResolutions`, [6](#0-5) ) — a repository whose conflicting content is crafted so the two scanners disagree on hunk count/boundaries will cause the model's hunk-indexed resolutions (`hunkResolutions[hunkIndex]` at [7](#0-6) ) to be spliced into the wrong location, or a conflict block that was never sent to the model to still be silently absorbed/removed during reassembly.

`validateResolutionPaths` ( [8](#0-7) ) only checks that the *count* of hunks the model echoed back matches the count `extractConflictHunks` computed — it does not, and cannot, verify that `reassembleResolvedFile`'s independent re-parse of the same raw file agrees on where those hunks actually are. This is exactly the H-03 pattern: two structures (hunk list used for the prompt/validation vs. marker positions used for reassembly) that are supposed to stay index-aligned but are produced by two different loops with different skip/`continue` semantics.

### Impact Explanation
If triggered, the reassembled file is written to disk (`writeFile(absolutePath, resolution.resolvedContent, 'utf8')` at [9](#0-8) ) and then `git add`ed ( [10](#0-9) ) as part of finishing a merge/rebase/cherry-pick. This is a silent corruption of what the user commits/pushes: content from one conflict block can be dropped, duplicated, or spliced into the wrong location, without any error surfaced to the user, because `validateResolutionPaths` only checks aggregate hunk counts and both parsers happen to "succeed" (just disagreeing on what a hunk is). This satisfies the stated valid-impact category of "silent corruption of what the user commits or pushes," driven by attacker-controlled repository/branch content (a malicious PR or remote branch designed to conflict with the victim's work using an adjacent/malformed marker sequence).

### Likelihood Explanation
Exploitation requires: (1) the victim has the Copilot conflict-resolution feature enabled and uses it to resolve a merge/rebase/cherry-pick conflict, and (2) the conflicting content (which the attacker fully controls via a crafted branch/PR that the victim merges or rebases against) contains a specific malformed-marker pattern (e.g., an unterminated `<<<<<<<` block immediately followed by a second well-formed block, or unusual `|||||||` placement) that makes the two independent scanners disagree. This is plausible without any local access, admin rights, or credential compromise — it only requires the victim to merge attacker-authored content and click through the Copilot resolution flow — but it is a narrow, marker-syntax-dependent trigger rather than a universally reachable path, so likelihood is moderate rather than certain.

### Recommendation
Use a single shared parser/data structure for both extracting conflict hunks and reassembling the resolved file, so hunk boundaries can never diverge between the two passes. At minimum, make `reassembleResolvedFile` reuse `extractConflictHunks`'s exact boundary logic (including its "consume nested markers as literal content" and malformed/EOF handling), and add a validation step that re-parses the original `rawContent` with the same extractor at reassembly time and asserts the hunk count/boundaries still match what was sent to the model before writing/staging the result — refusing to write and surfacing the conflict to the user instead of silently applying a possibly misaligned splice.

### Proof of Concept
1. Create a repository and produce a merge conflict where the conflicting region in a file looks like:
```
<<<<<<< HEAD
first block ours (no closing marker for this block)
<<<<<<< HEAD
second block ours
=======
second block theirs
>>>>>>> feature
```
2. Trigger the Copilot conflict resolution flow (merge/rebase) in Desktop on this file.
3. Observe that `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-242`) merges the first unterminated `<<<<<<<` with the *second* block's `>>>>>>> feature` into a single hunk whose `theirsContent` literally contains the second block's raw markers and text — the model is asked to resolve one hunk instead of two, and is fed structurally confusing/incorrect ours/theirs content.
4. When `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:559-596`) walks the file independently to splice the model's single resolution back in, verify (via unit-test-style invocation of the exported functions, as done in `app/test/unit/copilot-conflict-resolution-test.ts`) that the spliced-in content replaces the *entire* merged region — silently discarding the structure of the second, originally well-formed block — with no validation error raised, since `validateResolutionPaths` only compares hunk counts (1 expected vs 1 returned) and never re-verifies marker positions.

Note: I was not able to fully trace every downstream consumer of `IFileConflictContext.rawContent`/`skippedReason` (e.g., how `expectedFiles` passed into `validateResolutionPaths` is filtered for skipped files) within the available exploration, so the exact conditions under which this desync becomes committer-visible corruption versus a caught validation error should be confirmed with a live PoC run against the actual Desktop build before treating this as fully proven.

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

**File:** app/src/lib/copilot-conflict-context.ts (L367-461)
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

      // Guard against reading pathologically large files into memory. This is
      // a memory-safety bound only — resolvability is decided from the conflict
      // hunks below, not the whole-file size.
      try {
        const fileStat = await stat(absolutePath)
        if (fileStat.size > MAX_CONFLICT_FILE_READ_SIZE) {
          return {
            path: file.path,
            hunks: [],
            skippedReason: 'File too large to resolve automatically',
          }
        }
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      let content: string
      try {
        content = await readFile(absolutePath, 'utf8')
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File could not be read',
        }
      }

      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }

      // Gate on the size of the conflict content we'd actually send to the
      // model, not the whole-file size.
      const hunkSkipReason = getHunkSkipReason(hunks)
      if (hunkSkipReason !== null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: hunkSkipReason,
        }
      }

      return { path: file.path, hunks, rawContent: content }
    })
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L509-520)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-596)
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
    } else {
      resultLines.push(lines[i])
      i++
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L7196-7259)
```typescript
    for (const resolution of copilotResolutions) {
      if (manualResolutions.has(resolution.path)) {
        continue
      }

      // Delete-vs-modify conflicts are resolved by setting a manual
      // resolution (ours/theirs) rather than writing file content.
      // The existing stageManualConflictResolution flow handles the
      // actual git checkout --ours/--theirs and staging at commit time.
      if (resolution.deleteConflictAction !== undefined) {
        const file = state.changesState.workingDirectory.files.find(
          f => f.path === resolution.path
        )
        if (file === undefined) {
          continue
        }
        const deletedSide = getDeletedSideFromStatus(file)
        if (deletedSide === undefined) {
          continue
        }
        // "keep" → choose the non-deleted side, "delete" → choose the deleted side
        const manualChoice =
          resolution.deleteConflictAction === 'keep'
            ? deletedSide === 'ours'
              ? ManualConflictResolution.theirs
              : ManualConflictResolution.ours
            : deletedSide === 'ours'
            ? ManualConflictResolution.ours
            : ManualConflictResolution.theirs
        this._updateManualConflictResolution(
          repository,
          resolution.path,
          manualChoice
        )
        continue
      }

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

**File:** app/src/lib/stores/app-store.ts (L7262-7268)
```typescript
    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
```
