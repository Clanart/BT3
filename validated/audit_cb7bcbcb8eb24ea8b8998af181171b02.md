## Title
Attacker-controlled decoy conflict-marker text causes the Copilot auto-merge resolver to fuse unrelated conflict hunks, silently corrupting committed file content - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The external report is a smart-contract DoS caused by an unbounded, attacker-grown array being walked without validating array-length consistency between operations. The structural analog in GitHub Desktop is the Copilot-assisted merge-conflict resolver: it extracts conflict "hunks" from a file by scanning for `<<<<<<<`/`=======`/`>>>>>>>` markers (`extractConflictHunks`), sends each hunk to an LLM, and later splices the model's per-hunk output back into the original file by re-scanning for the *same* marker sequence and matching by ordinal position (`reassembleResolvedFile`), not by content or line offset. Both scans use a naive first-match strategy that does not verify that a `<<<<<<<`/`=======` pair belongs to the same logical conflict block it appears to close. Attacker-controlled file content (e.g. a documentation/tutorial file describing merge-marker syntax, or code deliberately crafted by a malicious contributor) that contains an unclosed decoy `<<<<<<<`/`=======` sequence ahead of a real conflict in the same file causes the two scanners to consume the real conflict's closing `>>>>>>>` as the decoy's own closing marker, merging a decoy region and a real conflict into a single "hunk." The model resolves this merged, garbled hunk, and its output is spliced back over the entire (decoy + real) span — silently discarding/altering the real, unrelated code the user was trying to merge, without any error or warning before the user commits.

### Finding Description
Conflict hunks are extracted with a purely lexical, order-based scan: [1](#0-0) 

`extractConflictHunks` walks lines looking for `<<<<<<<`, then greedily consumes everything until it sees `=======` (or `|||||||`), then greedily consumes everything again until it finds the *next* `>>>>>>>`, regardless of how many other marker-like lines it passes through: [2](#0-1) 

The reassembly step performs the mirror operation on the raw on-disk content, again matching the first `<<<<<<<` to the first later `>>>>>>>` it can find, splicing the model's per-hunk `resolvedContent` in between: [3](#0-2) 

Because both the extraction and reassembly scans use this same "first marker start, first later marker end" heuristic, an attacker who controls the content of a file that later ends up genuinely conflicted (e.g. a contributor's branch/PR merged by the victim, or any fork/branch fetched and merged) can insert a decoy, unclosed `<<<<<<<` ... `=======` block earlier in the file. When a real conflict later occurs further down in the *same file*, the decoy's scan does not stop at its own (missing) closing marker — it keeps consuming lines until it reaches the real conflict's `>>>>>>>`, since the regex only checks that a line begins with seven `>` characters and has no notion of which opening marker it belongs to. The result is that the decoy region and the entire real conflict are extracted and reassembled as one oversized, semantically garbled "hunk," and the LLM's single resolution for that blob is spliced over both regions, overwriting legitimate content and destroying the user's actual merge intent.

No verification exists anywhere in the pipeline that a hunk's ours/theirs boundaries are semantically consistent, that the model saw the same content that will be spliced back, or that the marker pairing used for extraction matches only well-formed, non-nested marker sequences. `validateResolutionPaths` only checks that hunk *counts* match between what was sent and what was returned — since both extraction and reassembly agree (wrongly) on the same collapsed hunk count, this check passes: [4](#0-3) 

The corrupted, model-resolved content is then written straight to disk and staged for commit with no diff review step forcing the user to notice the collapse: [5](#0-4) 

### Impact Explanation
This allows an attacker who can get a branch, PR, or file merged/fetched by the victim (no local access, no credentials, no social engineering beyond "the victim merges your branch," which is a normal Desktop workflow) to cause silent corruption of what the user ultimately commits and pushes. Legitimate code adjacent to (or overlapping in scan-order with) a decoy marker sequence can be deleted, mangled, or replaced by AI-hallucinated content without any conflict-marker warning surviving to alert the user, since the "still contains conflict markers" guard only checks the model's own output, not whether the *input hunk boundaries* were sane.

### Likelihood Explanation
Exploitability only requires that: (1) the attacker's branch/PR modifies a file to include a plausible, syntactically unclosed conflict-marker-like sequence (trivial to hide in documentation, code comments, or example blocks describing Git conflict syntax), and (2) that same file later has an actual conflict further down when the victim merges. This is a realistic scenario for any file commonly touched by multiple contributors (README/CONTRIBUTING docs, shared config files, changelog files). The Copilot conflict-resolution feature is an active, user-invoked feature in this codebase (`copilot-conflict-resolution.ts`, `copilot-conflict-context.ts`, `copilot-conflicts-loading-dialog.tsx`), and requires no unusual steps by the victim beyond using the "resolve conflicts with Copilot" option that is part of the app's normal merge/rebase/cherry-pick flow.

### Recommendation
- In `extractConflictHunks`, reject/skip a `<<<<<<<` block instead of scanning past it when a required marker (`=======`) is not found before either the next `<<<<<<<`/`>>>>>>>` or EOF, rather than allowing the "theirs" collection loop to run past subsequent, unrelated marker lines.
- Track marker nesting explicitly: if a second `<<<<<<<` is encountered before the current block's `>>>>>>>`, treat the first block as malformed and do not fuse the two regions.
- Make `reassembleResolvedFile` use the exact same hunk boundaries (e.g. line offsets) computed at extraction time rather than independently re-scanning the file, so extraction and reassembly are provably consistent instead of merely agreeing by coincidence of algorithm.
- Add a sanity check comparing the total line span consumed by "resolved" hunks against the original conflicted region sizes, and refuse to auto-apply (fall back to manual resolution) when hunks look abnormally large relative to what was shown to the user in the prompt/summary.

### Proof of Concept
1. Attacker submits a branch/PR that modifies `shared-file.md` to include, as ordinary content (e.g. inside a "How to resolve conflicts" doc section):
   ```
   <<<<<<< example
   sample ours text
   =======
   ```
   with no matching `>>>>>>>` anywhere near it.
2. Victim's own branch independently modifies a later part of `shared-file.md`, so merging attacker's branch produces a genuine conflict further down in the same file, delimited by real `<<<<<<<`, `=======`, `>>>>>>>` markers.
3. Victim opens the merge conflict and clicks "Resolve with Copilot."
4. `extractConflictHunks` starts at the decoy `<<<<<<<`, finds the decoy `=======`, then keeps scanning for the next `>>>>>>>` — which is the *real* conflict's closing marker — producing one hunk whose `oursContent`/`theirsContent` is a mash of the decoy text and the real conflicting content.
5. The model resolves this single garbled hunk; `reassembleResolvedFile` splices the result over the full span from the decoy `<<<<<<<` to the real `>>>>>>>`, discarding the real conflict's original ours/theirs distinction and any content structure in between.
6. `app-store.ts` writes this reassembled content to disk and stages it, so the user commits/pushes silently corrupted content unless they manually diff the entire spliced region themselves.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
```

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
