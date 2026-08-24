## Finding: Conflict-marker reassembly mismatch lets a crafted file corrupt Copilot-resolved commits

### Title
Marker re-parsing mismatch between conflict-hunk extraction and file reassembly allows attacker-controlled file content to leave stray/leftover diff text silently committed - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
GitHub Desktop's "Resolve with Copilot" feature extracts conflict hunks from a file with `extractConflictHunks` [1](#0-0)  and later splices the model's per-hunk resolutions back into the same raw file with a *second, independent* parser, `reassembleResolvedFile` [2](#0-1) . Both scanners locate a hunk's closing boundary by testing each line against the same `>{7}` regex and stopping at the *first* match, but they disagree on what happens to content between a spurious marker-like line and the real Git-inserted closing marker. A repository whose (attacker-influenced) file content contains conflict-marker-like text that is not an actual Git conflict marker (e.g. documentation/tutorial text demonstrating `<<<<<<<`/`=======`/`>>>>>>>` syntax) can cause the two passes to agree on a truncated hunk boundary, after which the genuine trailing conflict text — including the real `>>>>>>>` marker — is copied through verbatim as ordinary file content by `reassembleResolvedFile`.

### Finding Description
`extractConflictHunks` builds the model's view of a conflict: for the "theirs" side it scans forward and stops at the first line matching `theirsMarker` (`/^>{7}(?:\s|$)/`) [3](#0-2) , with no verification that this is the marker Git actually inserted for this hunk (e.g. by checking the trailing label or hunk ordinance).

Independently, `reassembleResolvedFile` re-derives hunk boundaries from the same raw content using the same first-match strategy: it scans forward from `<<<<<<<`, and breaks as soon as it sees a line matching `reassemblyTheirsMarker` [4](#0-3) . Since both passes use identical "stop at first `>{7}` line" logic, they agree on a truncated boundary whenever the file contains a marker-lookalike line before the real closing marker — but neither pass has any mechanism to recover the *real* remaining conflict content once this happens.

The consequence is in the "resume" branch of `reassembleResolvedFile`: after replacing everything up to the (possibly spurious) closing marker with the model's single resolved hunk, parsing resumes at `closingIndex + 1` and continues treating subsequent lines as ordinary content, since only `<{7}` lines are recognized as the start of a new marker block [5](#0-4) . Any genuine leftover diff text — including Git's real `>>>>>>>` marker line and unresolved "theirs" content that followed the spurious marker — is copied through **verbatim** into the final reassembled string.

That reassembled string is written straight to disk and staged with no further validation for leftover conflict-marker text: [6](#0-5) 

The only marker-content guard that exists, in `parseCopilotConflictResolution`, checks the **model's own** `resolvedContent` string for markers [7](#0-6)  — it never re-checks the fully reassembled file for leftover marker text that the splicer itself failed to consume. `validateResolutionPaths` likewise only checks that the hunk *count* returned by the model matches the count `extractConflictHunks` computed [8](#0-7)  — a coincidentally-matching count does not guarantee the two independent parsers agreed on where each hunk actually starts/ends in the raw text.

### Impact Explanation
An attacker who can get content merged into a repository the victim later conflicts with (e.g. a contributed branch, a forked PR, or any file containing conflict-marker-like example text such as a git tutorial, ASCII-art divider, or code sample) can engineer a real merge/rebase/cherry-pick conflict on that file such that Copilot's reassembly path silently leaves a stray, syntactically-invalid `>>>>>>>` line (and any trailing unresolved diff text) in the file that Desktop then writes to disk and `git add`s on the user's behalf. This is a silent corruption of what the user commits/pushes — the resulting commit can contain literal, unremoved Git conflict-marker syntax without any warning surfaced in the UI, potentially breaking builds, reintroducing dropped code, or (in interpreted languages/config files) altering runtime behavior of what gets shipped.

### Likelihood Explanation
This requires no special repository or account privileges — only the ability to have a file with marker-lookalike content merged/fetched normally, and for a genuine conflict to subsequently occur on/near it, which is a routine event in collaborative development. The bug is purely a logic mismatch between two regex-based scanners operating on the same untrusted input; it does not depend on the AI model's behavior (the model is only asked to resolve whatever truncated hunk it's shown) and triggers deterministically once the crafted marker-lookalike text and a real conflict coincide.

### Recommendation
Use a single, shared parsing routine (not two independently-maintained scanners) for both extracting hunks and locating splice boundaries, so extraction and reassembly can never disagree. After reassembly, re-scan the final resolved file content for residual `<{7}`/`={7}`/`>{7}` marker lines and treat their presence as a hard validation failure (skip the file / fall back to manual resolution) rather than writing it to disk. Track and assert that the exact byte-ranges consumed during extraction match those replaced during reassembly (e.g. by capturing line offsets in `IConflictHunk` and replacing by offset instead of by re-scanning).

### Proof of Concept
1. Create a file `CONTRIBUTING.md` (or any file) containing literal, non-conflict example text that looks like part of a conflict:
```
Some intro text.

Example of a conflict marker block:
<<<<<<< HEAD
sample ours
=======
sample theirs
>>>>>>> example-branch

More real content that must survive.
```
2. On `branch-a`, edit "Some intro text." to "Intro text A."; on `branch-b`, edit it to "Intro text B." — creating a genuine conflict whose real markers land *before* the literal example block, e.g. after merge:
```
<<<<<<< HEAD
Intro text A.
=======
Intro text B.
>>>>>>> branch-b

Example of a conflict marker block:
<<<<<<< HEAD
sample ours
=======
sample theirs
>>>>>>> example-branch

More real content that must survive.
```
3. Trigger `startCopilotConflictResolution`. `extractConflictHunks` will parse this as containing hunks whose boundaries are derived purely from first-match regex scanning, and `reassembleResolvedFile` independently re-derives the same (or a diverging) truncated boundary using `reassemblyTheirsMarker` — because the "example" `>>>>>>> example-branch` line satisfies the exact same regex as a real closing marker, content after the first hunk's actual replacement point can be miscomputed.
4. Craft the placement/nesting further (multiple real+fake marker lines interleaved) to make `reassembleResolvedFile`'s single forward scan stop at the wrong `>{7}` line for a given `<{7}` start; confirm via unit test that the returned string still contains a literal `>>>>>>>` or `=======` line that was never part of any `IHunkResolution.resolvedContent`, and that `_applyCopilotConflictResolutions` writes this content straight to disk and stages it with `git add` with no leftover-marker check. [2](#0-1) [1](#0-0) [6](#0-5)

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L443-448)
```typescript
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
      }
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L549-598)
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
