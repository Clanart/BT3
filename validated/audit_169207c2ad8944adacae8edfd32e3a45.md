### Title
Attacker-controlled branch content can desync conflict-hunk boundaries in Copilot conflict resolution, silently corrupting the committed file - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`reassembleResolvedFile` splices Copilot's per-hunk resolutions back into a conflicted file by walking the raw on-disk content line-by-line and re-detecting conflict-marker boundaries with three regexes (`<{7}`, `={7}`, `>{7}`), matching resolutions to markers *by order only, not by identity*. [1](#0-0)  Because a merge/rebase/cherry-pick can pull in content from a branch/PR the attacker fully controls, the "ours"/"theirs" body of a real conflict can itself contain lines that coincidentally (or deliberately) match these same marker patterns. This desynchronizes the naive boundary scan from the actual git-generated markers, causing the wrong resolution to be spliced into the wrong location, or leaving raw `<<<<<<<`/`=======`/`>>>>>>>` marker text embedded as literal committed code — while the app reports the conflict as fully resolved.

### Finding Description
`reassembleResolvedFile` looks for an opening marker (`^<{7}(?:\s|$)`), then scans forward for a `=======` line and the **first** subsequent line matching `^>{7}(?:\s|$)` to determine the end of the block: [2](#0-1) 

```
for (let j = i + 1; j < lines.length; j++) {
  if (reassemblySeparatorMarker.test(lines[j])) {
    hasSeparator = true
  } else if (reassemblyTheirsMarker.test(lines[j])) {
    closingIndex = j
    break
  }
}
```

This scan stops at the *first* line that looks like a closing marker, regardless of whether it is git's actual generated marker (`>>>>>>> branch-name`) or just a line of content on the "ours"/"theirs" side that happens to start with seven `>` characters followed by whitespace/EOL (e.g. quoted diff/patch text, ASCII banners, or a line an attacker deliberately crafts in their branch to exploit this). Two corruption paths follow directly from this:

1. **Decoy closing marker appears before the real separator** — `hasSeparator` is still `false` when the scan breaks, so the block is classified "malformed" and only the opening `<<<<<<<` line is treated as plain content; the walk resumes from `i+1`. [3](#0-2)  Since the main loop only special-cases lines matching the *opening* marker, the real `=======` separator and the real `>>>>>>>` closer (further down) are never recognized as markers on the second pass — they get copied through as ordinary text. The result: raw conflict-marker text (`<<<<<<<`, `=======`, `>>>>>>>`) ends up embedded verbatim in the file the app claims is "fully resolved" (all conflict markers removed, per `IFileResolution.resolvedContent`'s contract). [4](#0-3) 

2. **Decoy closing marker appears after the real separator but before the real closer** — the block is truncated early at the decoy line. The genuine remainder of the "theirs" content plus the true `>>>>>>>` marker get pushed straight into `resultLines` as normal file content (not stripped), and `hunkIndex` is advanced by only one for what git treated as a single conflict. Any subsequent real conflict blocks in the file are now off-by-one against `hunkResolutions`, so **the wrong AI-generated resolution gets spliced into a different, unrelated conflict location** in the file. [5](#0-4) 

`validateResolutionPaths` only checks that the *count* of hunks the model returned matches the count reported by whatever upstream conflict parser built `IFileConflictContext.hunks` (used to prompt the model). [6](#0-5)  It never re-validates that `reassembleResolvedFile`'s own independent, weaker line-based marker detector agrees with that same count or with marker positions, and it never checks that the final reassembled content is free of leftover marker lines. The two parsers (the one that builds prompt context/hunk count, and the one that reassembles the final write) are separate, non-shared implementations, so there is no invariant tying "N conflicts the model was told about" to "N conflicts `reassembleResolvedFile` actually finds when writing the file" (I was unable to fully inspect `app/src/lib/copilot-conflict-context.ts` to confirm the exact marker-parsing logic used there, but grep confirms it independently implements its own marker/hunk extraction separate from `reassembleResolvedFile`).

This is the direct analog of the BigBang bug: two places compute a supposedly-identical quantity (hunk/conflict count and position) via different logic paths, and nothing enforces they stay equal; the mismatch silently corrupts the tracked state (there: `userBorrowPart` vs. minted `amount`; here: the reassembled file content vs. the actual conflict structure) while the caller believes the operation completed cleanly.

### Impact Explanation
The corrupted value is the file content the user is about to `git add`/commit as the merge/rebase/cherry-pick resolution. Silent corruption here means either (a) literal git conflict-marker syntax (`<<<<<<<`, `=======`, `>>>>>>>`) gets committed into source files without the user noticing, since the resolution dialog shows the file as fully resolved, or (b) code from one part of the file's resolution gets misapplied to a different, unrelated conflict location, changing program behavior without the user's awareness or consent. Both outcomes match "silent corruption of what the user commits or pushes" and originate from a git remote/branch the attacker controls (their PR/branch content becomes part of "theirs" in the merge), requiring no local access, no admin rights, and no unnatural user action beyond merging/reviewing a PR that legitimately conflicts with the user's branch — a normal Desktop workflow.

### Likelihood Explanation
Exploitation requires the attacker to control the content of one side of a merge (a PR branch, a fetched remote branch, or a rebase/cherry-pick source) that is designed to produce a conflict with the victim's branch, and to include within that content a line matching `^>{7}(?:\s|$)` or `^={7}$` (e.g., a code comment, ASCII divider, embedded diff/patch snippet, or documentation about git conflict markers — all plausible, innocuous-looking content an attacker fully controls). This is a realistic, low-effort setup: no privilege escalation, no social engineering beyond a routine merge of attacker-authored branch content, and it is entirely reachable through Desktop's advertised Copilot conflict-resolution feature.

### Recommendation
Do not use ad hoc single-line regex scanning to re-locate conflict boundaries when reassembling. Reuse the same, single source of truth for conflict-block boundaries used to build the prompt context (`IFileConflictContext.hunks`), passing exact line ranges/offsets for each hunk into `reassembleResolvedFile` instead of re-parsing raw text a second time with weaker heuristics. Additionally, after reassembly, assert the output contains zero lines matching the marker patterns before treating a file as resolved, and fail loudly (surface an error requiring manual resolution) rather than silently emitting content with markers or mis-mapped resolutions.

### Proof of Concept
1. Attacker opens a PR/branch that modifies a shared file so it will conflict with the victim's branch. Inside the "theirs" hunk content (the attacker's own change), the attacker includes an innocuous-looking line such as a divider comment: `// >>>>>>> notes` (7 `>` chars followed by a space) placed after the real content but before git's actual closing marker would appear — or more simply between the true `<<<<<<<`/`=======` block, exploiting the fact any line starting with 7 `>` chars plus whitespace matches `reassemblyTheirsMarker`.
2. Victim uses Desktop to merge/rebase and hits a real conflict in this file; Copilot conflict resolution is invoked, correctly reporting N real conflicts to the model based on the "true" parser and receiving N correct hunk resolutions.
3. During reassembly, `reassembleResolvedFile` walks the raw file: it detects the real `<<<<<<<` opener but the boundary scan set out in [2](#0-1)  stops at the attacker's decoy `>>>>>>>`-looking line instead of git's real closing marker, truncating the block early.
4. The real remaining conflict text (part of "theirs", the genuine `=======`/`>>>>>>>` markers) is pushed through as ordinary file content instead of being replaced, and/or subsequent hunk resolutions are spliced at the wrong offsets due to the `hunkIndex` desync.
5. The dialog reports the file fully resolved; the victim commits/pushes a file containing leftover conflict-marker text or an incorrectly relocated code change, without any warning.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L26-33)
```typescript
/** Resolution suggestion for a single conflicted file. */
export interface IFileResolution {
  /** Repository-relative file path that was resolved. */
  readonly path: string
  /** The fully resolved file content (all conflict markers removed). */
  readonly resolvedContent: string
  /** Human-readable explanation of how and why conflicts were resolved this way. */
  readonly reasoning: string
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-551)
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
export function reassembleResolvedFile(
  rawContent: string,
  hunkResolutions: ReadonlyArray<IHunkResolution>
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
