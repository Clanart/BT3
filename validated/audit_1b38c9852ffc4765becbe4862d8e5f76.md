### Title
Conflict-marker parser divergence between `extractConflictHunks` and `reassembleResolvedFile` lets an attacker-controlled merge branch cause Copilot conflict resolution to silently commit files with real conflict markers left in place - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`copilot-conflict-resolution.ts` and `copilot-conflict-context.ts` each implement their own, independently-written scanner for `<<<<<<<`/`=======`/`>>>>>>>` conflict-marker blocks. `extractConflictHunks` is used to build the prompt sent to the AI model and to compute the "expected hunk count" used for validation; `reassembleResolvedFile` is used afterwards, on the very same raw file content, to splice the model's per-hunk resolutions back in "by order, not by line number." [1](#0-0)  Because the two parsers use slightly different rules for what counts as a "well-formed" conflict block, a crafted file can be seen as one resolvable conflict by `extractConflictHunks` but as an *unrecognized/malformed* block by `reassembleResolvedFile`. When that happens the reassembly path copies the literal, unresolved git conflict markers straight through into the "resolved" file, which is then written to disk, staged, and reported to the user as resolved — corrupting the committed content silently.

### Finding Description
`extractConflictHunks` walks a file looking for `<<<<<<<`, then collects "ours" lines until it sees a line matching `baseMarker` or `separatorMarker` (`=======`), then collects "theirs" lines until it sees the **first** line matching `theirsMarker` (`>>>>>>>`): [2](#0-1) 

Nothing in the "ours" loop checks for a `>>>>>>>`-looking line — such a line, if it appears before the real `=======`, is simply treated as ordinary "ours" content, and the whole span up to the *first* subsequent closer is still counted as **one well-formed hunk**.

`reassembleResolvedFile`, however, requires — within its look-ahead from the `<<<<<<<` line — that a `=======`-looking line be seen *before* the first `>>>>>>>`-looking line is encountered, or it declares the block malformed and passes it through untouched: [3](#0-2) 

Specifically:
```
if (!hasSeparator || closingIndex === -1) {
  // Malformed marker — copy through as regular content
  resultLines.push(lines[i])
  i++
  continue
}
```
`closingIndex` is set to the position of the **first** line matching `>>>>>>>`, and the loop `break`s as soon as that is found — even if no `=======` has been seen yet. So if a line that matches the `>>>>>>>` regex appears inside the "ours" section (before the real separator), `reassembleResolvedFile` finds it as the closer, `hasSeparator` is still `false`, and the entire block is rejected as malformed.

Concretely, for a file containing:
```
<<<<<<< HEAD
line A
>>>>>>> fake-embedded
line B
=======
line C
>>>>>>> feature
```
- `extractConflictHunks` returns **1 hunk** (`oursContent = "line A\n>>>>>>> fake-embedded\nline B"`, `theirsContent = "line C"`), which is what gets sent to the model and used to compute `expectedHunkCounts` in `validateResolutionPaths`.
- `reassembleResolvedFile` sees `>>>>>>> fake-embedded` as the closing marker (before ever reaching the real `=======`), decides the block is malformed, and pushes every one of these lines — including the real `<<<<<<< HEAD`, `=======`, and `>>>>>>> feature` markers — through **verbatim** into the "resolved" output.

`validateResolutionPaths` only checks that the *count* of hunks the model returned matches `extractConflictHunks(...).length`; it never cross-checks that count against what `reassembleResolvedFile` would actually locate in the same content: [4](#0-3) . So validation passes even though the two parsers disagree about the file's structure.

The final content is then written straight to disk and staged with no post-reassembly check that the output no longer contains conflict markers: [5](#0-4) 

The only marker check that exists is on the *model's individual hunk output* (`rc`) during parsing, not on the final reassembled file: [6](#0-5) .

### Impact Explanation
The corrupted value is the on-disk/staged file content produced by `reassembleResolutions` → `writeFile(...)` → `git add`. Because the file is staged, Desktop's UI and git status both treat it as resolved, and the user is free to commit and push it. The pushed commit then contains literal, unresolved git conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) embedded in otherwise real source files — this typically breaks compilation/parsing of the file for every downstream consumer of the repository, and can be crafted deliberately by an attacker who controls the incoming branch/PR (e.g., by adding an innocuous-looking line that happens to match `>{7}` at the start of a line inside a conflicting hunk) to sabotage a victim's automated Copilot-assisted conflict resolution without any indication in the UI that something went wrong.

### Likelihood Explanation
This requires the victim to use the "Resolve with Copilot" conflict-resolution feature on a merge/rebase/cherry-pick against a branch whose conflicting hunk contains an attacker-crafted line that matches the 7-character `>>>>>>>` marker pattern at the start of a line — a small, plausible piece of content to slip into a PR (e.g. embedded example diff output, ASCII art, or a quoted patch in a comment/changelog). No local access, elevated privileges, or social engineering beyond "the attacker's branch gets merged/rebased against" is required; the bug is purely in how Desktop's own dual conflict-marker parsers disagree, so existing validation (`validateResolutionPaths`) does not catch it.

### Recommendation
Do not maintain two independent conflict-marker scanners. `reassembleResolvedFile` should either (a) accept the exact hunk boundaries computed once by `extractConflictHunks` (positions/line ranges) instead of re-scanning the raw text with a different, laxer/stricter rule set, or (b) share the identical marker-detection state machine used by `extractConflictHunks`. Additionally, after reassembly, verify the final `resolvedContent` for every file contains no residual `<<<<<<<`/`=======`/`>>>>>>>` marker lines before writing/staging it, and fail loudly (surface the file as unresolved / skipped) rather than silently committing whatever was produced.

### Proof of Concept
1. Start a merge/rebase where the incoming branch (attacker-controlled) modifies a file so that the resulting conflict looks like:
```
<<<<<<< HEAD
line A
>>>>>>> fake-embedded
line B
=======
line C
>>>>>>> feature
```
2. Use Desktop's "Resolve with Copilot" flow. `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-243`) reports this as a single, valid hunk and sends it to the model; the model returns one `resolvedContent` for it. `validateResolutionPaths` passes because 1 expected hunk == 1 returned hunk.
3. `reassembleResolutions` calls `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) on the same raw content. Because `>>>>>>> fake-embedded` is found before any `=======`, the block is judged malformed and copied through verbatim, including the real conflict markers.
4. The app-store write path (`app/src/lib/stores/app-store.ts:7233-7267`) writes this content to disk and runs `git add` on it, and the UI shows the file as resolved.
5. The user commits and pushes; the pushed file still literally contains `<<<<<<< HEAD` / `=======` / `>>>>>>> feature` markers baked into tracked source, with no warning ever shown.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L443-449)
```typescript
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
      }
      validatedHunks.push({ resolvedContent: rc })
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-548)
```typescript
/**
 * Reassemble a fully resolved file by splicing per-hunk resolutions into
 * the original file content (which still has conflict markers on disk).
 *
 * Walks the original file line-by-line. Non-conflicted lines are copied
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
 * exactly, and the model's output is only responsible for the small
 * resolved sections.
 *
 * A `<<<<<<<` line that is not followed by both a `=======` separator and
 * a closing `>>>>>>>` before EOF is treated as regular file content (not a
 * conflict block) and copied through unchanged to avoid data loss from
 * malformed or stray markers.
 *
 * @param rawContent - The full file content on disk, including conflict markers
 * @param hunkResolutions - Per-hunk resolved content, in the order they appear in the file
 * @returns The reassembled file with all conflicts resolved
 */
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

**File:** app/src/lib/copilot-conflict-context.ts (L186-242)
```typescript
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
