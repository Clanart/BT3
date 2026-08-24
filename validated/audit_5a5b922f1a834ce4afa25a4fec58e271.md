## Title
Attacker-controlled merge content with an embedded fake `>>>>>>>`-style line desynchronizes Copilot conflict-hunk extraction from file reassembly, causing silent corruption/leftover conflict markers in the file the user commits - (`File: app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
GitHub Desktop's "Resolve with Copilot" feature parses conflict markers twice with two independently-implemented, naive regex-based scanners: once to build the prompt context (`extractConflictHunks`) and once to splice the model's response back into the file (`reassembleResolvedFile`). Both scanners treat *any* line beginning with the correct number of marker characters as a real delimiter, with no correlation to which side of the merge produced it. Because the "theirs" content is fully attacker-controlled (it's whatever is on the branch/commit being merged, rebased, or cherry-picked from a cloned/fetched, untrusted repository), an attacker can plant a line that looks like a closing conflict marker (`>>>>>>> ...`) inside their own content. This causes the two scanners to disagree about where the conflict hunk ends, so the reassembled file that gets written to disk and `git add`-ed silently contains leftover raw diff text and a literal conflict-marker string that was never reviewed or resolved.

### Finding Description
`extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:122-165, 179-279`) walks the file line by line using:
```
const oursMarker = /^<{7}(?:\s|$)/
const theirsMarker = /^>{7}(?:\s|$)/
``` [1](#0-0) 
When collecting the "theirs" side of a hunk, it stops at the **first** line matching `theirsMarker`, regardless of whether that line was actually inserted by git or is simply attacker-authored file content that happens to match the pattern: [2](#0-1) 

Separately, `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:523-599`) re-scans the *raw on-disk file* with the same marker patterns to decide where to splice the model's resolved content back in: [3](#0-2) 

Both parsers are logically identical and both are naive: they cannot distinguish a genuine git-inserted `>>>>>>> branch` marker from a line of attacker-supplied file content that merely matches `/^>{7}(?:\s|$)/`. Since the entire "theirs" text between `=======` and the true closing marker is content the attacker fully controls (it's their commit's version of the conflicting lines), they can embed a fake marker line in the middle of it. Both scanners then terminate the hunk early at the fake marker — consistently with each other — meaning `validateResolutionPaths` (`app/src/lib/copilot-conflict-resolution.ts:473-521`) sees matching hunk counts and does not catch anything wrong: [4](#0-3) 

The genuinely-real trailing content (the rest of "theirs", and the real `>>>>>>> feature` marker git actually inserted) is left in the file untouched by both the model and the reassembly splice logic, since neither parser recognizes it as part of the (truncated) hunk anymore. It is copied through verbatim as ordinary text by the fallback branch of `reassembleResolvedFile`: [5](#0-4) 

The per-hunk sanity check only inspects the *model's own* resolved fragment for re-introduced markers — it cannot see this leftover text because it never belongs to any hunk in the model's eyes: [6](#0-5) 

Finally, `_applyCopilotConflictResolutions` writes the fully reassembled content straight to disk and stages it with no post-reassembly check that the result is free of stray conflict-marker text or truncated diff fragments: [7](#0-6) 

### Impact Explanation
This breaks the invariant that "the file written by Copilot conflict resolution is a fully-resolved, marker-free version of the two real branches' content." Instead, the committed file can silently contain:
- Truncated/incorrect data (part of the real "theirs" content is dropped from what the model even saw, so its resolution is based on incomplete information), and
- Leftover literal conflict-marker text (e.g. `>>>>>>> feature`) and un-reviewed raw diff fragments spliced into otherwise normal source code, which then gets `git add`-ed and is one click ("Continue Merge") away from being committed and pushed.

This matches the "silent corruption of what the user commits or pushes" impact class: an attacker who merely gets their branch/commit merged, rebased, or cherry-picked (a fork PR, an upstream branch, any cloned/fetched repository content) can manipulate what ends up in the victim's commit without the victim's source files or git history ever indicating anything was wrong beyond a stray line of text.

### Likelihood Explanation
The trigger only requires the victim to (1) merge/rebase/cherry-pick a branch containing attacker-controlled content that conflicts with local changes, and (2) click "Resolve with Copilot" then "Continue Merge" — an intended, unprivileged Desktop workflow requiring no unusual user action, no local access, and no prior compromise. The only "special" step is that the attacker must place an innocuous-looking line matching `/^>{7}(?:\s|$)/` (7+ `>` characters followed by whitespace or end-of-line) somewhere in the conflicting region of their own branch content — trivial to craft and easy to disguise (e.g., as a comment, a string literal, or ASCII art) inside a source file that will conflict with the victim's local edits.

### Recommendation
- Make `extractConflictHunks` and `reassembleResolvedFile` share a single, git-aware conflict-hunk parser (ideally reuse Desktop's existing, more robust conflict-marker detection used elsewhere for manual merges) instead of two independent naive regex scanners.
- After reassembly, validate that the final `resolvedContent` contains none of the four conflict-marker patterns (`<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`) at column 0 before writing to disk; refuse to auto-apply and fall back to manual resolution if any are found.
- Consider anchoring marker detection to the exact ref names git generated for that conflict (e.g., matching `>>>>>>> <expected-branch-or-sha>`) rather than a bare character-count regex, since arbitrary attacker content matching a bare `>{7}` pattern is easy to produce.

### Proof of Concept
1. Attacker creates a branch where a file has a line inside the conflicting region that reads exactly `>>>>>>> injected` (7 `>` chars followed by a space), followed by more legitimate-looking content.
2. Victim, in Desktop, merges/rebases/cherry-picks that branch onto local changes that touch the same lines, producing a real git conflict:
   ```
   <<<<<<< HEAD
   ours line
   =======
   their line 1
   >>>>>>> injected
   their line 2
   >>>>>>> feature
   after
   ```
3. Victim clicks "Resolve with Copilot". `extractConflictHunks` truncates `theirsContent` to `"their line 1"`, stopping at the fake `>>>>>>> injected` line (`app/src/lib/copilot-conflict-context.ts:228-242`).
4. The model resolves based on the truncated hunk. `reassembleResolvedFile` independently finds the same (fake) closing marker at line 5 and splices the model's output there (`app/src/lib/copilot-conflict-resolution.ts:559-596`), leaving `"their line 2"` and the literal string `>>>>>>> feature` in the final file content, unmodified and unreviewed by the model.
5. Victim clicks "Continue Merge" in `copilot-conflicts-dialog.tsx`, which calls `applyCopilotConflictResolutions` → `writeFile` + `git add` (`app/src/lib/stores/app-store.ts:7233-7268`), committing the corrupted file containing residual diff text and a stray conflict-marker string.

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L491-521)
```typescript
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
