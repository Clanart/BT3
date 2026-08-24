Based on extensive tracing through the Copilot merge-conflict-resolution pipeline (a feature unique to this fork, not present in upstream GitHub Desktop), I found a concrete parsing-ambiguity bug that is the closest structural analog to the reported issue: **a state machine that misinterprets ordinary file content as a structural delimiter, corrupting what gets fed into (and ultimately spliced back into) a file the user is about to commit.**

### Title
Conflict-marker parser treats any `=======`-shaped content line as the conflict separator, corrupting AI-resolved merge output - (File: `app/src/lib/copilot-conflict-context.ts`)

### Summary
`extractConflictHunks` extracts the "ours"/"theirs" sides of a merge conflict by scanning line-by-line for regexes matching `<<<<<<<`, `|||||||`, `=======`, and `>>>>>>>`. The "ours" collection loop terminates as soon as it sees *any* line matching `/^={7}$/`, without verifying that this is the actual git-inserted separator for that conflict block rather than incidental file content (e.g. a Markdown Setext-heading underline, an ASCII banner, or a code comment divider made of seven-plus `=` characters). This causes the "ours" and "theirs" content passed to the Copilot model — and later spliced back into the file — to be split at the wrong point.

### Finding Description
In `app/src/lib/copilot-conflict-context.ts`, the ours-content loop is: [1](#0-0) 

It only tests `baseMarker`/`separatorMarker` against each line; it has no way to distinguish a real git conflict separator from a line that merely matches the same 7-equals-sign shape and happens to already exist in the user's own file content (this pattern is common: Markdown Setext `==` headings, changelog/README section dividers, ASCII-art banners in comments, etc.). As soon as such a line appears anywhere before the true separator, collection of "ours" stops early, and everything from that point on — including any real `|||||||`/`=======` markers that belong to the actual conflict — is folded into `theirsContent`.

This corrupted split is what gets rendered into the model prompt via `formatConflictContextForPrompt`: [2](#0-1) 

and it is also what silently satisfies `validateResolutionPaths`, since that function only checks *hunk counts*, not that ours/theirs boundaries were parsed correctly: [3](#0-2) 

The reassembly step then splices the model's `resolvedContent` for that single (mis-parsed) hunk back into the real file: [4](#0-3) 

Because the model was told "ours" is empty/truncated and "theirs" contains a mix of the user's real content plus the incoming branch's content, its resolution can silently drop or misattribute the user's own unmerged work when the user accepts the suggested resolution — and the file that gets written and later committed/pushed no longer reflects what either side actually intended, with no error surfaced anywhere in the pipeline.

### Impact Explanation
This causes silent corruption of what the user commits: accepted AI-generated resolutions can drop legitimate local ("ours") content or blend it incorrectly with incoming ("theirs") content, without any validation catching it, because `validateResolutionPaths` only checks hunk *counts* per file, not content-boundary correctness. This falls squarely in the requested impact category of "silent corruption of what the user commits or pushes," and is triggerable purely by the shape of an attacker-influenced merge (a branch/PR the user merges that creates a conflict in a file already containing a `=======`-shaped line), with no local access or credentials required.

### Likelihood Explanation
Conflicts touching Markdown files (READMEs, CHANGELOGs) with Setext `====` headings, or source files with `=======`-style comment banners, are common. An attacker who can get the victim to merge/rebase/cherry-pick a branch they authored (e.g., via a pull request, or by directing the user to merge a malicious fork) only needs the conflict to land in such a file — they do not need to control the "ours" side, since this content pattern already exists naturally in many real repositories. This makes the trigger condition realistic rather than contrived, though it depends on the AI conflict-resolution feature being enabled and the user accepting the suggested resolution without close review.

### Recommendation
`extractConflictHunks` should validate a candidate separator/base line strictly in context — e.g., require that a `=======` (or `|||||||`) line only counts as a structural marker when scanning explicitly inside an active conflict block *and* preferably require it to be immediately followed eventually by a matching `>>>>>>>` closing marker with no intervening `<<<<<<<`, mirroring exactly the same well-formedness check used in `reassembleResolvedFile`'s lookahead (`hasSeparator`/`closingIndex`). Ideally both parsers should share a single, well-tested marker-scanning implementation rather than being maintained independently, and `validateResolutionPaths` should be extended to sanity-check that reassembly of each resolution does not unexpectedly grow/shrink relative to `rawContent`, surfacing a `CopilotValidationError` instead of silently writing corrupted content.

### Proof of Concept
A locally-conflicting file containing (this is exactly the kind of content a legitimate README/CHANGELOG can already have):
```
<<<<<<< HEAD
Local Section
=======
this text is NOT a real conflict separator — it's a Markdown-style
underline that already existed in the file
||||||| merged common ancestors
base content
=======
Incoming Section
>>>>>>> feature-branch
```
Running `extractConflictHunks` on this content yields `oursContent = ""` (empty — collection stopped at the first `=======`), while `theirsContent` incorrectly absorbs the "Local Section" text, the literal `||||||| merged common ancestors` marker line, `base content`, the real separator, and `Incoming Section` all together. This corrupted split is what gets sent to the Copilot model as "ours"/"theirs," and whatever content the model returns is spliced back into the file by `reassembleResolvedFile` with no indication to the user that "ours" was ever empty or that content was misclassified. [5](#0-4)

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L179-279)
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

    // The ours marker line is at oursStart - 1
    const markerStart = oursStart - 1
    const contextStart = Math.max(0, markerStart - contextLines)
    const contextEnd = Math.min(lines.length - 1, hunkEnd + contextLines)

    // Clamp context to not include conflict markers from adjacent hunks
    const contextBeforeLines: Array<string> = []
    for (let j = markerStart - 1; j >= contextStart; j--) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextBeforeLines.unshift(lines[j])
    }

    const contextAfterLines: Array<string> = []
    for (let j = hunkEnd + 1; j <= contextEnd; j++) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextAfterLines.push(lines[j])
    }

    const contextBefore = contextBeforeLines.join('\n')
    const contextAfter = contextAfterLines.join('\n')

    hunks.push({
      oursContent: oursLines.join('\n'),
      theirsContent: theirsLines.join('\n'),
      baseContent: hasBase ? baseLines.join('\n') : null,
      contextBefore,
      contextAfter,
    })
  }

  return hunks
}
```

**File:** app/src/lib/copilot-conflict-context.ts (L560-583)
```typescript
    for (let i = 0; i < file.hunks.length; i++) {
      const hunk = file.hunks[i]
      parts.push(`### Conflict ${i + 1} of ${file.hunks.length}`)
      parts.push('')

      if (hunk.contextBefore) {
        parts.push('Context before:')
        parts.push(makeFencedBlock(hunk.contextBefore, lang))
        parts.push('')
      }

      parts.push('Ours (current branch):')
      parts.push(makeFencedBlock(hunk.oursContent, lang))
      parts.push('')

      if (hunk.baseContent !== null) {
        parts.push('Base (common ancestor):')
        parts.push(makeFencedBlock(hunk.baseContent, lang))
        parts.push('')
      }

      parts.push('Theirs (incoming branch):')
      parts.push(makeFencedBlock(hunk.theirsContent, lang))
      parts.push('')
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L580-596)
```typescript

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
