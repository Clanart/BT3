## Finding: Copilot conflict resolution misclassifies literal marker-like text as real conflict hunks, letting attacker-controlled file content get silently spliced/deleted

### Title
Copilot merge-conflict auto-resolution treats any line matching `/^<{7}(?:\s|$)/` … `/^>{7}(?:\s|$)/` as a real conflict marker, letting an attacker-crafted file silently corrupt unrelated content when it is spliced into the resolved file — (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
Both the hunk-extraction step (used to build the LLM prompt) and the reassembly step (used to write the resolved file back to disk) detect "conflict markers" purely by regex on individual lines — `<{7}`, `|{7}`, `={7}`, `>{7}` — with no verification that these lines were actually inserted by Git for a real conflict in that region. Any file that legitimately contains such sequences as text content (documentation about Git conflicts, a test fixture, a tutorial, a string literal) will have that text misparsed as a conflict hunk whenever the file is *also* flagged conflicted by Git for an unrelated reason. Copilot's fabricated "resolution" for that fake hunk is then spliced verbatim over the original content and written to disk and staged for commit — a silent corruption of content that was never actually part of the merge conflict.

### Finding Description
`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` splits the whole file content into lines and treats any line matching the marker regexes as a genuine conflict delimiter: [1](#0-0) [2](#0-1) 

This function is invoked on the **entire raw file content** read from disk for every file Git reports as conflicted, with no distinction between text that Git actually inserted at a conflict site and text elsewhere in the file that happens to match the pattern: [3](#0-2) 

The extracted hunks are sent to Copilot for resolution, and `validateResolutionPaths` only checks that the *count* of returned hunks matches the *count* extracted — it has no way to know whether any of those "hunks" were real: [4](#0-3) 

The reassembly step, `reassembleResolvedFile`, independently re-scans the same raw content using the same marker regexes and splices in the model's resolution for every marker block it finds, in order: [5](#0-4) 

Finally, the reassembled content is written straight to disk and staged via `git add`, with no diff/review step scoped to only the genuinely-conflicted region: [6](#0-5) 

Because neither stage validates that a detected marker line originates from an actual Git-inserted conflict (e.g. by cross-checking against the real conflict region reported by `git status`/index stages, or by requiring markers to appear in a specific, git-generated structural pattern), an attacker who controls a branch/PR that a victim merges can plant a legitimate-looking marker-shaped string elsewhere in a file that also has a real (unrelated) conflict. When the user runs Desktop's Copilot-assisted conflict resolution, the fake "hunk" is included in the prompt, resolved by the model, and its output overwrites the attacker-chosen span of the file — content the user never saw as part of the actual conflicting hunk.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": the victim believes only the real conflict was resolved and reviewed, but an attacker-influenced region of an otherwise clean file can be silently replaced. Because the write path (`app-store.ts`) stages the file directly (`git add`) after the LLM-driven rewrite, the corruption can be committed and pushed without the developer noticing, since the result dialog's diff/summary is keyed to what Copilot says it changed, not to a verified real-conflict boundary.

### Likelihood Explanation
Requires: (1) the attacker's branch is merged/rebased by the victim causing a genuine conflict in a file, and (2) that same file (or another conflicted file) contains attacker-crafted content matching the marker regex outside the real conflict. Such content is unremarkable in real repositories (docs about Git conflict resolution, string literals demonstrating the format — this very repository's own test fixtures contain exactly this shape of content, e.g. `app/test/unit/copilot-conflict-context-test.ts`), so it does not require unnatural user interaction, only a routine merge/rebase using the Copilot conflict-resolution feature.

### Recommendation
Do not rely solely on line-pattern matching to identify conflict marker blocks. Cross-validate detected marker regions against Git's own conflict data (e.g., diff3 output or `git ls-files -u` stage boundaries) so only actually-conflicting regions are sent to Copilot and spliced back, and reject/flag any marker-shaped line found outside a verified conflict region instead of treating it as resolvable content.

### Proof of Concept
1. Attacker pushes a branch where `docs/git-tips.md` contains (as ordinary prose, not a real conflict):
```
Here is what a merge conflict looks like:
<<<<<<< HEAD
example ours
=======
example theirs
>>>>>>> branch
```
2. The same branch also modifies another line in `docs/git-tips.md` such that merging it into the victim's branch produces a genuine, unrelated conflict elsewhere in the same file (so Git marks the file as conflicted, e.g. `UU`).
3. Victim merges and invokes "Resolve with Copilot." `buildConflictContext` → `extractConflictHunks` (`copilot-conflict-context.ts:179-279`) parses **two** hunks: the real one and the fake "example ours/theirs" text.
4. Copilot returns a resolution for both hunks (validation only checks hunk count, `copilot-conflict-resolution.ts:473-521`).
5. `reassembleResolvedFile` (`copilot-conflict-resolution.ts:549-599`) splices Copilot's fabricated content over the "example ours/theirs" prose, replacing legitimate documentation text.
6. `app-store.ts:7233-7259` writes the corrupted file and stages it — the user commits content they never actually reviewed as a diff.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-context.ts (L179-215)
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

```

**File:** app/src/lib/copilot-conflict-context.ts (L429-447)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L549-599)
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
}
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
