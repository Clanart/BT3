# Title
Attacker-controlled "theirs" branch content can desync Copilot conflict-marker parsing and cause silent corruption of a resolved-and-committed file - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The "Resolve with Copilot" AI conflict-resolution feature parses git conflict markers out of a conflicted file's raw text using line-anchored regexes, once when building the model prompt (`extractConflictHunks`) and again independently when splicing the model's answer back into the file (`reassembleResolvedFile`). Both parsers locate the *closing* `>>>>>>>` marker by scanning for the first line that matches the marker regex, with no cross-check against git's actual marker offsets. Because the "theirs" side of a conflict is literally the content of a fetched/merged branch that an attacker fully controls, an attacker can plant a line that incidentally (or deliberately) matches the closing-marker pattern inside otherwise ordinary file content. This desyncs both parsers at the same point, causing the final reassembled file — which is written straight to disk and `git add`-ed without re-validation — to either drop real content or retain a literal stray conflict-marker line, silently corrupting what the user commits and pushes.

### Finding Description
Conflict markers are recognized purely by regex, independent of git's own marker bookkeeping: [1](#0-0) 

`extractConflictHunks` collects "theirs" content by scanning forward until it hits any line matching the theirs-marker regex — it does not require that a `=======` separator immediately preceded it, nor does it verify the marker text (branch name) matches the actual merge: [2](#0-1) 

If the attacker's branch content (which becomes the literal "theirs" text between the real `=======` and the real `>>>>>>>`) contains any line of exactly seven `>` characters followed by whitespace/EOL (e.g. a documentation snippet illustrating git conflict syntax, ASCII-art divider, or deliberately crafted junk), the loop stops there instead of at git's real closing marker. `theirsContent` is truncated, and the remainder of the true theirs text plus the genuine `>>>>>>> branch` marker line is left un-consumed as ordinary file content for the rest of the scan.

The same fragility exists independently in the reassembly path used to splice the model's answer back into the original on-disk content: [3](#0-2) 

This loop looks ahead for a `=======` separator and then the *first* line matching the theirs-marker regex; once both are seen it treats that as the real closing marker and splices the model's `resolvedContent` there, regardless of whether that line is git's actual marker or attacker-planted lookalike text. Everything from the true closing marker onward (in this scenario, the true remaining theirs text plus a leftover literal `>>>>>>> theirLabel` marker line) is then copied through verbatim as "regular content" in the next iterations of the outer `while` loop.

Nothing downstream re-validates the *final reassembled file*. `validateResolutionPaths`/`parseCopilotConflictResolution` only check that the **model's own** per-hunk `resolvedContent` strings don't contain conflict markers: [4](#0-3) 

There is no equivalent check on the fully reassembled file before it is written and staged: [5](#0-4) 

So a reassembled file that now contains a stray literal `>>>>>>> <branch>` line (or has silently lost real content past the fake marker) is written to disk and `git add`-ed with no further scrutiny, then presented to the user in the "Continue Merge" flow as an already-resolved file.

### Impact Explanation
The attacker's payload lives entirely inside content that ends up on the "theirs" side of a merge/rebase/cherry-pick — i.e. inside a branch, PR, or commit the victim fetches and merges, which is exactly the "attacker controls a cloned/fetched repository" primitive called out as in-scope. The result is not a mere failed operation: the file the victim believes Copilot fully resolved is committed and pushed with either (a) missing genuine "theirs" content past the fake marker, or (b) a literal, uncleaned git conflict-marker line embedded in tracked source, both of which are silent corruptions of what the user commits/pushes — the victim sees a clean "Continue Merge" flow and no indication that the write path or `_applyCopilotConflictResolutions` skipped/mis-scoped part of the file.

### Likelihood Explanation
This requires: (1) the victim to use the Copilot "Resolve with Copilot" feature on a conflict touching an attacker-influenced file, and (2) the attacker's branch content to contain a line matching the exact 7-character marker pattern (`^>{7}(?:\s|$)`) positioned inside what becomes the theirs-side conflict text. This is a narrow but realistic trigger — such patterns can appear in ASCII banners, changelogs, or docs describing git conflicts (the repo's own fixture `repository-with-HEAD-file` shows the project is already aware such coincidental collisions with git's marker conventions occur in real repos: [6](#0-5) ). No local access, elevated privileges, or social engineering beyond a normal merge/PR review is needed.

### Recommendation
- In `extractConflictHunks`, require that the theirs-closing marker only be honored after a `=======` was actually consumed for that specific hunk, and prefer matching git's actual marker text (which typically includes the branch/commit label after the `>>>>>>>`) rather than a bare regex on marker length.
- In `reassembleResolvedFile`, avoid re-deriving conflict boundaries via independent regex scanning; instead reuse the exact hunk boundaries already computed by `extractConflictHunks`/`buildConflictContext` (index/offset based), so both phases agree on where each hunk starts and ends.
- Add a final safety check before `writeFile`/`git add` in `_applyCopilotConflictResolutions` that rejects (or falls back to skipping) any reassembled file that still contains conflict-marker-shaped lines or whose line count materially diverges from expectations, rather than trusting only the model's raw hunk output.

### Proof of Concept
1. Attacker pushes a branch where a file (e.g. `docs/GIT_TIPS.md`) contains an innocuous-looking line consisting of exactly `>>>>>>> \n` (7 `>` chars followed by a space) embedded a few lines into the file, e.g. inside a fenced example showing "what a conflict marker looks like."
2. Victim fetches this branch and merges/rebases it into their own branch, producing a genuine conflict on that same file (both sides modified it near the top).
3. On resolving with "Resolve with Copilot," `buildConflictContext` → `extractConflictHunks` scans the theirs section and, in `app/src/lib/copilot-conflict-context.ts:229-237`, stops at the attacker's fake `>>>>>>> ` line instead of git's real closing marker, truncating `theirsContent` sent to the model.
4. On reassembly, `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts:559-591` independently locates the same fake line as the "closing marker," splices the model's answer there, and copies the remaining true theirs text plus the real `>>>>>>> branch` marker line through as ordinary file content.
5. `_applyCopilotConflictResolutions` writes this reassembled content straight to disk and stages it (`app/src/lib/stores/app-store.ts:7258-7267`) with no further conflict-marker validation on the final file, so the victim commits and pushes a file containing a stray literal conflict-marker line and/or missing content.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L443-448)
```typescript
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
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

**File:** app/test/fixtures/repository-with-HEAD-file/README.md (L1-4)
```markdown
# Repository with HEAD file

So it turns out if you have a file in your Git repository named the same as HEAD
you will probably confuse Git unless you are explicit with your commands.
```
