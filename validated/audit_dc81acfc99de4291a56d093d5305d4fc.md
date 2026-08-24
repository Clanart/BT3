Confirmed the full path: `_applyCopilotConflictResolutions` in `app/src/lib/stores/app-store.ts` writes `resolution.resolvedContent` straight to disk via `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` and stages it with `git add`, right before the user clicks "Continue Merge" and commits [1](#0-0) . This closes the loop from attacker-controlled file content to a committed/staged result.

### Title
Fixed-length (exactly 7-character) conflict-marker regexes let attacker-crafted file content corrupt Copilot's ours/theirs hunk boundaries, silently mis-resolving and auto-staging conflicted files - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
GitHub Desktop's Copilot-assisted conflict resolution parses conflicted files using hand-written regexes that assume Git conflict markers are *always exactly* 7 characters (`<{7}`, `={7}`, `>{7}`), with no anchoring against the actual conflict boundaries Git itself will honor (Git's `conflict-marker-size` `.gitattributes` attribute lets marker length vary, and ordinary/malicious file content can coincidentally or deliberately contain lines that satisfy this exact pattern). This is structurally the same defect class as the reported issue: an unprivileged, attacker-influenced input (file content instead of `asset_decimals`) is insufficiently validated before being fed into a computation (hunk boundary extraction instead of stableswap scaling), producing a result that is silently wrong and hard for the user to detect before they accept it — here, before the resolved content is written to disk and staged for commit.

### Finding Description
`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` locates hunk boundaries using: [2](#0-1) 

These are simple regex tests on raw line text with no validation that the `=======` line found while collecting "ours" content is actually *the* separator belonging to the currently-open `<<<<<<<` block, as opposed to any other line in the file that happens to consist of exactly seven `=` characters (e.g., a Markdown Setext H1 underline, a documentation snippet showing example conflict markers, a code "divider" comment line, or content an attacker deliberately places in a branch that is likely to be merged). The "collect ours" loop breaks on the *first* line matching `separatorMarker`, regardless of whether it is the real separator for the enclosing conflict: [3](#0-2) 

If a spurious 7-equals-sign line sits inside the real "ours" (or "theirs") region of an actual conflict, the function silently mis-splits the hunk: part of the true "ours" text becomes "theirs" (or vice versa) in the structured `IConflictHunk` sent to the model. There is no check that the counted content is internally consistent (e.g. that only one separator was seen, that content before/after looks plausible), so this corruption is undetectable downstream — the very issue the audit report calls out about `asset_decimals` never being checked against reality before being trusted.

That corrupted hunk is what gets sent to Copilot as "ours"/"theirs" content in `buildConflictContext`: [4](#0-3) 

The model's `resolvedContent` for that hunk is then spliced back verbatim by `reassembleResolvedFile`, which independently re-scans for the *true* `<<<<<<<`...`>>>>>>>` block (so the splice itself is well-formed) but has no way to know the model reasoned about scrambled ours/theirs content: [5](#0-4) 

The only sanity check applied to the model's output is that it doesn't still contain `<{7}` + `={7}` marker text — it does not validate that the resolution logically corresponds to the real ours/theirs sides: [6](#0-5) 

Finally, this resolved content is written straight to the working tree and staged, with the write path explicitly trusting that a file still showing conflict markers has not been fixed by any other means: [7](#0-6) 

### Impact Explanation
An attacker who controls content that ends up merged into the victim's working tree (a malicious/compromised feature branch, a PR the user merges, or content pulled from a fetched remote) can seed a file with lines that match the marker regexes exactly (`^={7}$`, `^<{7}(?:\s|$)`, `^>{7}(?:\s|$)`) inside a region that will become part of a real merge conflict. When the victim later uses "Resolve with Copilot," the hunk boundaries fed to the AI are wrong, so the AI's "resolved" content can silently drop, duplicate, or misattribute real changes from one side of the conflict. Because `reassembleResolvedFile` only guarantees the *true* conflict-marker envelope is fully replaced (not that the replacement is logically correct), and the marker-absence check is the only automated safety net, the final file looks completely clean — no markers, normal diff — while its logical content has been corrupted based on attacker-influenced structure. The corrupted content is then auto-written to disk and `git add`-ed by `_applyCopilotConflictResolutions`, right before the user commits/pushes, matching the report's "silent corruption of what the user commits" impact bucket.

### Likelihood Explanation
This requires: (1) the victim to use the "Resolve with Copilot" feature (opt-in, but a normal one-click action from the manual conflicts dialog), and (2) a conflict to occur on a file where the attacker previously introduced a line matching the exact marker pattern somewhere in the neighborhood of a later real conflict. Neither condition requires local/physical access, leaked credentials, or unusual user steps — an attacker only needs a merged/fetched branch with crafted file content, the same threat model as the original report's permissionless pool creation. The narrow "exactly 7 characters" requirement is a design invariant an attacker can trivially satisfy deliberately (and, less reliably, could also occur by accident), so this is a plausible, non-obvious path that a normal review of the resulting diff is unlikely to catch, since the output contains no conflict markers or other visible red flag.

### Recommendation
- Track the actual marker line indices found during "ours"/"theirs"/"base" collection and validate structural well-formedness (exactly one separator, markers not nested) before trusting the split; reject/skip hunks where more than one `=======`-looking line appears inside a single conflict block instead of silently taking the first one.
- Make marker detection consistent with Git's actual behavior, e.g. by deriving/normalizing marker length via `git check-attr conflict-marker-size` (or by reusing Git's own status/diff output) rather than hardcoding `{7}`.
- After reassembly, perform a lightweight consistency check (e.g., diff the reassembled file against `ours`/`theirs` blobs to ensure the resolution doesn't silently discard content that exists in neither side, or surface a stronger warning/diff preview before the "Continue Merge" write-and-stage step).

### Proof of Concept
1. Attacker pushes a branch containing a file, e.g. `NOTES.md`, with content:
```
Feature docs
=======
Some section that will be part of "ours" during a later conflict
```
2. Victim's local branch modifies the same region differently, and victim merges the attacker's branch, producing a real conflict:
```
Feature docs
<<<<<<< HEAD
Some section that will be part of "ours" during a later conflict
=======
Victim's replacement text
>>>>>>> attacker-branch
```
3. `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:200-214`) encounters the pre-existing `=======` line while still collecting "ours" content, ends `oursLines` early, and misclassifies the remainder (including the real `Victim's replacement text`) as "theirs" — even though it's not.
4. Copilot receives a scrambled `oursContent`/`theirsContent` pair for this hunk and produces a `resolvedContent` based on the wrong understanding of which side is which.
5. `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:559-596`) finds the true outer `<<<<<<<`...`>>>>>>>` envelope correctly and splices in Copilot's (logically wrong) content, producing a clean-looking file with no markers.
6. `_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7258-7268`) writes this file to disk and `git add`s it; the user clicks "Continue Merge" and commits/pushes the silently corrupted result.

### Citations

**File:** app/src/lib/stores/app-store.ts (L7241-7268)
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

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-context.ts (L200-214)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L440-447)
```typescript
      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }
```

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
