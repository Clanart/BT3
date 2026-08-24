## Title
Embedded fake conflict-marker text in a merged file causes Copilot conflict resolution to silently drop real conflicting content and commit stray merge-marker garbage - (`app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`extractConflictHunks` (used to build the prompt sent to Copilot) and `reassembleResolvedFile` (used to splice the model's response back onto disk) both locate conflict-block boundaries with simple line-anchored regexes for `<<<<<<<`, `=======`, and `>>>>>>>`. Neither function verifies that the markers it matches actually belong to the *outermost* real conflict produced by git — if the "ours"/"theirs" side of a genuine conflict itself contains lines that happen to match these exact patterns (e.g. documentation or fixtures that literally show git conflict-marker syntax), both functions latch onto the first embedded marker instead of the true one. The result is a hunk boundary mismatch between what is sent to the model and what really needs resolving, and `reassembleResolvedFile` ends up splicing the model's answer over only part of the real conflict while leaving the rest of the actual competing content — plus literal `=======`/`>>>>>>>` remnants — as plain text in the file that is then written to disk and `git add`-staged with no verification.

### Finding Description
`extractConflictHunks` walks the file line by line and, once it sees an "ours" marker, collects lines until it sees the *first* line matching `separatorMarker` (`/^={7}$/`) or `baseMarker`, then collects until the *first* line matching `theirsMarker` (`/^>{7}(?:\s|$)/`): [1](#0-0) 

This scan has no concept of "the outer, currently-open hunk" — any line elsewhere in the file that happens to match those exact 7-character marker patterns at the start of a line (not embedded mid-line, which the parser does correctly ignore per its own test at `app/test/unit/copilot-conflict-context-test.ts:326-344`) is treated as if it were the real separator/closing marker for the hunk currently being parsed.

`reassembleResolvedFile`, which later splices the model's resolved text back into the on-disk content, does the analogous lookahead independently: [2](#0-1) 

It looks ahead from an `<<<<<<<` line for the first `=======` and the first `>>>>>>>` and treats everything between them as "the conflict block" to replace with `hunkResolutions[hunkIndex]`. Both functions independently stop at the *first* marker-shaped line they encounter, so if a file's real "ours" or "theirs" content legitimately contains marker-shaped lines (git-marker documentation, test fixtures for merge tooling, generated diff snippets, etc.), both the extraction and the reassembly boundaries get truncated to the *inner*, fake conflict block rather than the real, outer one.

Concretely, for content shaped like:
```
<<<<<<< HEAD
example of a conflict:
<<<<<<< HEAD
fake ours
=======
fake theirs
>>>>>>> feature
end of real ours
=======
real theirs
>>>>>>> feature
```
`extractConflictHunks` returns a single hunk bounded by the *inner* fake markers (`oursContent` including the literal `<<<<<<< HEAD`/`fake ours` lines, `theirsContent` = `"fake theirs"`), and everything from `end of real ours` through the true closing `>>>>>>> feature` is left outside any hunk and therefore never surfaced to the model or the user as needing resolution.

`reassembleResolvedFile` mirrors this: it finds `hasSeparator` at the fake `=======` and `closingIndex` at the fake `>>>>>>>`, replaces that whole span with the model's answer for the single hunk it was given, and then simply copies through the remaining lines (`end of real ours`, `=======`, `real theirs`, `>>>>>>> feature`) verbatim because they no longer start with an ours marker.

The result written to disk contains the model's resolution glued to leftover, unresolved conflict syntax (`=======`, `>>>>>>> feature`) and is missing the true competing content that existed between the real opening marker and the real separator.

This file is then written and staged with no re-validation that all marker lines have actually been removed: [3](#0-2) 

The only existing guard in this path checks whether the file was *externally* resolved (no more conflict status from git) to avoid clobbering manual edits — it does not check whether Copilot's own output still contains stray marker text before writing and calling `git add`.

### Impact Explanation
This falls under "silent corruption of what the user commits" — the exact category called out as valid impact. The user clicks "Continue Merge" believing the conflict was intelligently resolved; instead the committed file (a) permanently loses legitimate content from one side of the real conflict, and (b) contains stray, syntactically-invalid `=======`/`>>>>>>>` leftover lines baked into the committed source, which is then pushed. This can silently break builds, reintroduce reverted code, or drop security-relevant changes from the losing side of the conflict without any error being surfaced — the merge appears to succeed cleanly in the UI.

### Likelihood Explanation
The attacker precondition is realistic and requires no special privileges: an attacker who can get a branch merged/rebased against (e.g. a PR contributor, or any co-collaborator on a shared branch) only needs one of their files to (a) have a genuine, unrelated conflict with the victim's changes and (b) contain, anywhere in the "ours" or "theirs" text of that conflict, line-anchored text that looks like a conflict marker (`<<<<<<< ...`, `=======`, or `>>>>>>> ...`) — plausible in documentation about git, generated diff/patch snippets, or test fixtures for merge tooling. The victim need only use the "Resolve with Copilot" feature on that conflict.

### Recommendation
Make `extractConflictHunks` and `reassembleResolvedFile` track marker nesting/state consistently (e.g. once inside an ours-block, only the *matching* separator/closing marker for the currently-open block should end it, and any further `<<<<<<<`-shaped line encountered before the current block closes should either be rejected as malformed or explicitly disambiguated), and share a single marker-parsing implementation between context-building and reassembly so they can never diverge. Additionally, before writing resolved content to disk in `_applyCopilotConflictResolutions`, re-scan `resolution.resolvedContent` for any residual marker-shaped lines and refuse to write/stage if any are found, surfacing the file back to the user instead.

### Proof of Concept
1. Attacker prepares a file `docs/MERGING.md` (or any source file) on their branch that both:
   - conflicts with the victim's local change on one section, and
   - contains, in the *content* of one side of that conflict, literal marker-shaped lines documenting/demonstrating conflict resolution, e.g.:
     ```
     <<<<<<< HEAD
     Example section showing conflict markers:
     <<<<<<< HEAD
     fake ours line
     =======
     fake theirs line
     >>>>>>> feature
     Real content that should win
     =======
     Attacker's real competing content
     >>>>>>> feature
     ```
2. Victim merges/rebases the attacker's branch in GitHub Desktop, hits the conflict, and clicks "Resolve with Copilot".
3. `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-278`) parses only the inner fake block as the hunk sent to the model.
4. `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) splices the model's answer into the same inner fake span and leaves `Real content that should win`, `=======`, `Attacker's real competing content`, `>>>>>>> feature` untouched.
5. `_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7233-7268`) writes this content and runs `git add` on it with no marker check.
6. The victim commits/pushes a file that both dropped legitimate content and contains stray `=======`/`>>>>>>>` text, with no warning from Desktop.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L200-242)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-582)
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
