### Title
Naive conflict-marker regex matching lets attacker-controlled file content be misidentified as a merge conflict, causing Copilot's resolution to silently overwrite unrelated committed content - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`extractConflictHunks` and `reassembleResolvedFile` locate merge-conflict regions purely by regex-matching lines that look like `<<<<<<<`, `=======`, `|||||||`, `>>>>>>>`, with no verification that these markers were actually inserted by Git at a real conflict boundary. Any file whose *content* (from either merge side, both of which can be attacker-controlled via a malicious branch/PR the user merges) happens to contain lines matching these patterns — e.g. documentation showing example conflict markers, or a divider line of exactly seven `=` characters — is treated as a genuine unresolved conflict. When the user invokes "Resolve with Copilot," that spurious "hunk" is sent to the model and its returned text is spliced back into the file and written to disk/staged automatically, silently corrupting content the user never intended to touch and had no real conflict.

### Finding Description
`extractConflictHunks` scans line-by-line for `oursMarker` (`^<{7}(?:\s|$)`), collects "ours" until it sees a `separatorMarker` (`^={7}$`) or `baseMarker`, then collects "theirs" until a `theirsMarker` (`^>{7}(?:\s|$)`): [1](#0-0) 

This logic has no way to distinguish an actual Git-inserted conflict boundary from ordinary file content that coincidentally matches the same regexes (e.g. a plain divider line of seven equals signs, or a tutorial/CONTRIBUTING file that literally shows `<<<<<<< HEAD` / `=======` / `>>>>>>> branch` as illustrative text). Any such text — introduced by either merge side, both of which are attacker-controlled when the user merges a malicious branch or pull request — is parsed as a real conflict hunk and included in the context sent to the model: [2](#0-1) 

The model's response for that (spurious) hunk is later spliced back into the file by `reassembleResolvedFile`, which uses the exact same naive marker regexes to re-locate the block and substitute in the resolved content, matched purely "by order, not by line number": [3](#0-2) [4](#0-3) 

Finally, the reassembled content is written straight to the working file and staged with `git add`, with no diff review requirement forcing the user to notice that unrelated content was replaced: [5](#0-4) 

Existing guards do not stop this path:
- `getHunkSkipReason` only checks the size of extracted content, not whether it corresponds to a real conflict. [6](#0-5) 
- The "malformed marker" check in `reassembleResolvedFile` only verifies that a `<<<<<<<`-looking line is followed by a `=======` and `>>>>>>>` before treating it as a block — it doesn't verify the block is an actual git conflict, so well-formed-looking documentation text passes this check just as easily as a real conflict. [7](#0-6) 
- The "resolved externally" skip in `_applyCopilotConflictResolutions` only checks whether the *whole file* still has unresolved conflicts per Git's status, not whether each individual extracted hunk maps to one of Git's actual conflict regions. [8](#0-7) 

### Impact Explanation
Since this flow runs automatically as part of `_applyCopilotConflictResolutions` writing directly to the working-tree file and staging it, an attacker who controls one side of a merge (a malicious branch, fork, or PR the victim merges) can cause the Copilot-assisted merge to silently rewrite unrelated file content — for example replacing correct documentation/example text, license text, or code containing coincidental marker-like lines with AI-hallucinated content — without any error or clear visual indication, since Git itself never flagged that region as conflicted. This is a silent corruption of what the user ultimately commits and pushes, potentially affecting shared history.

### Likelihood Explanation
Exploitation only requires the attacker to control the textual content of a branch/PR that will be merged by the victim (a normal, unprivileged Git operation) — no local access, malware, or leaked credentials are needed. Lines consisting of seven or more `=`, `<`, or `>` characters are a very plausible, unremarkable occurrence (divider comments, ASCII art, tutorials about git conflict markers), making accidental or intentional triggering realistic whenever the victim actually has a genuine conflict elsewhere in the same repository and opts to use "Resolve with Copilot."

### Recommendation
Do not rely purely on regex pattern-matching of arbitrary file text to identify conflict hunks. Instead, derive hunk boundaries from Git's own conflict data (e.g. `git diff` conflict info, or index stages 1/2/3 via `git show :1:/:2:/:3:`) so that only lines Git itself flagged as conflicted are ever sent to the model or spliced back into the file. At minimum, cross-validate any regex-detected "hunk" against `git status`/`ls-files -u` output for that path before treating it as resolvable content.

### Proof of Concept
1. Attacker opens a PR/branch containing a file, e.g. `docs/git-tips.md`, with a section such as:
   ```
   Example of a conflict:
   <<<<<<< HEAD
   your version
   =======
   their version
   >>>>>>> feature
   ```
   as plain illustrative text (no real conflict).
2. Victim merges this branch and separately has a genuine, unrelated conflict in the same repository (any file), triggering the "Resolve with Copilot" flow.
3. `buildConflictContext` → `extractConflictHunks` reads `docs/git-tips.md`, finds the illustrative markers, and treats them as a real hunk (`app/src/lib/copilot-conflict-context.ts:179-242`), sending "your version"/"their version" to the model as if they were in conflict.
4. The model returns a "resolution" for this fake hunk; `reassembleResolvedFile` splices it into `docs/git-tips.md` (`app/src/lib/copilot-conflict-resolution.ts:549-598`).
5. `_applyCopilotConflictResolutions` writes the rewritten `docs/git-tips.md` to disk and stages it (`app/src/lib/stores/app-store.ts:7233-7260`), even though Git never considered this file part of any conflict — the illustrative content is now silently replaced in the resulting commit.

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

**File:** app/src/lib/copilot-conflict-context.ts (L294-315)
```typescript
export function getHunkSkipReason(
  hunks: ReadonlyArray<IConflictHunk>
): string | null {
  let totalContent = 0

  for (const hunk of hunks) {
    const sides = [hunk.oursContent, hunk.theirsContent, hunk.baseContent ?? '']
    for (const side of sides) {
      totalContent += side.length
      for (const line of side.split('\n')) {
        if (line.length > MAX_CONFLICT_LINE_LENGTH) {
          return 'Conflict contains lines too long to resolve automatically'
        }
      }
    }
    if (totalContent > MAX_CONFLICT_CONTENT_SIZE) {
      return 'Conflict region too large to resolve automatically'
    }
  }

  return null
}
```

**File:** app/src/lib/copilot-conflict-context.ts (L440-460)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-546)
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

**File:** app/src/lib/stores/app-store.ts (L7233-7260)
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
```
