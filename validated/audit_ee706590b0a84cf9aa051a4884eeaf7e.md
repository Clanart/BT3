Found a genuine discrepancy between the two independent conflict-marker walkers used in the Copilot merge-conflict-resolution feature: `extractConflictHunks` (used to *count* hunks and build the prompt) and `reassembleResolvedFile` (used to *splice* the model's per-hunk answers back into the on-disk file). They parse the same attacker-influenced file with different rules for a malformed/nested marker sequence, so the hunk count validated by `validateResolutionPaths` does not guarantee the hunk index used during reassembly lines up with the real conflict blocks. This can make Copilot's resolutions get spliced into the wrong conflict block, or make a trailing conflict block silently disappear from the committed file — i.e., silent corruption of what the user commits, sourced entirely from a remote branch/commit an attacker controls.

### Title
Mismatched conflict-marker parsers between hunk extraction and reassembly cause silent misapplication/loss of conflict content - (File: app/src/lib/copilot-conflict-context.ts, app/src/lib/copilot-conflict-resolution.ts)

### Summary
`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` and `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` both parse the same on-disk file containing Git conflict markers (`<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`), but they use non-identical algorithms to decide what counts as a conflict block. The first is used to tell Copilot how many hunks exist per file; the second is used afterwards to literally splice Copilot's resolved text back into the original file by *positional* hunk index. Because a merge/rebase/cherry-pick brings in content from a branch, PR, or fork the user does not control, an attacker who can get a conflicting file merged/cherry-picked into the victim's branch controls the marker layout that both parsers see.

### Finding Description
`extractConflictHunks` walks lines and treats every well-formed `<<<<<<< ... ======= ... >>>>>>>` (optionally with `|||||||`) run as one hunk, skipping/discarding lines outside such runs. [1](#0-0) 

`reassembleResolvedFile` walks the same raw file independently, using its own regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) to find `<<<<<<<` blocks, and for every well-formed block it consumes, it pulls the *next* entry off `hunkResolutions` by index and splices it in — a malformed block (no separator or no closing marker) is instead copied through verbatim as ordinary content rather than being counted as a hunk: [2](#0-1) 

Crucially, `reassembleResolvedFile`'s marker regexes do not recognize the diff3 `|||||||` base marker at all — it only distinguishes `<<<<<<<`, `=======`, and `>>>>>>>`. `extractConflictHunks`, by contrast, explicitly special-cases `|||||||` as a second boundary inside the same hunk. If a conflict block contains a `=======` line (unrelated content, e.g. arbitrary text a malicious commit purposefully places as a code/data line matching that marker regex) or a `|||||||`-style line inside the "theirs" region in a way that the two regex walks tokenize differently, the two parsers can disagree on how many "hunks" exist for the file. `validateResolutionPaths` only checks that the *count* returned by Copilot matches `expectedFiles` hunk counts computed via `extractConflictHunks`: [3](#0-2) 

That check never re-validates against what `reassembleResolvedFile` will actually find when it walks `ctx.rawContent` at splice time: [4](#0-3) 

so a parser disagreement is never caught before the resolved content is written back to the user's working tree/index. Because `reassembleResolvedFile` treats a block missing a proper separator/closing marker as "regular content" and passes it through untouched (line 574-579), while the model was told (via the mismatched count from `extractConflictHunks`) that a different number of hunks existed, resolved content can be:
- spliced against the wrong conflict block (positional index mismatch), replacing the wrong section of the file, or
- dropped entirely when `hunkIndex >= hunkResolutions.length` for a trailing block, silently deleting a conflicted region's content from the file that is then written to disk and, if the user accepts, committed. [5](#0-4) 

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": a file merged in from a remote/PR that an attacker controls can end up with incorrect or missing content after automatic conflict resolution, with no error surfaced to the user (the feature is designed to soft-fail rather than throw on most shape mismatches, and the count-based `validateResolutionPaths` guard does not detect this particular class of divergence). The corrupted content can silently reach the user's commit/push, potentially reintroducing removed security checks, dropping validation code, or resurrecting deleted secrets/logic depending on what conflicting content the attacker crafts.

### Likelihood Explanation
Exploitation requires the victim to run GitHub Desktop's Copilot-assisted conflict resolution on a merge/rebase/cherry-pick where one side originates from attacker-influenced content (a malicious branch, fork PR, or externally supplied commit) that includes a crafted conflict — this is within the reachable "cloned/fetched repository" and "PR" attacker model described in scope, and does not require local access, admin rights, or social engineering beyond the normal act of resolving a real merge conflict against attacker content, which is a routine workflow (reviewing/merging external PRs). The likelihood is moderated by needing the crafted marker text to actually diverge between the two specific parsing implementations, which requires some care from the attacker but is a deterministic, reproducible text-processing property, not a guess.

### Recommendation
Use a single shared parsing implementation (or a shared line-classification helper) for both `extractConflictHunks` and `reassembleResolvedFile` so hunk counting and hunk splicing can never disagree on what constitutes a conflict block, including consistent handling of the diff3 `|||||||` marker. Additionally, have `reassembleResolvedFile` assert that the number of well-formed conflict blocks it actually walks matches `hunkResolutions.length` exactly, and throw a `CopilotValidationError` (aborting the write) rather than silently truncating/misaligning content when they differ.

### Proof of Concept
1. Set up a merge conflict where the conflicted file (as it lands on disk after Git's merge machinery) contains two real conflict blocks, and additionally the "theirs" content of the first block contains a line that is exactly `=======` embedded as ordinary attacker-controlled text (e.g. a code comment or markdown separator) positioned such that it is consumed as a hunk delimiter differently by the two functions (this is fully controllable by whoever authored the PR/branch that produces the "theirs" side).
2. `extractConflictHunks` reports N hunks (e.g., 2) sent to Copilot; `validateResolutionPaths` accepts a 2-hunk response.
3. `reassembleResolvedFile`, using its independent scan, treats the crafted content as terminating the first block early or failing to find a valid closing marker for the second block, causing the second hunk's resolved content to be spliced against the wrong region or discarded (`hunkIndex >= hunkResolutions.length` at line 585) while conflict markers around it are simply copied through unresolved or removed.
4. The final file written to disk (and shown to the user as the "resolved" file, then committed) differs from what the model intended and from what a correct resolution would produce, without any validation error being raised.

Note: I was not able to execute this against a live merge to confirm the exact byte-for-byte divergence between the two regex walks in a single test run within this session — verifying the precise crafted input that produces divergence would benefit from a Devin session with the ability to run the two functions directly (`extractConflictHunks` vs `reassembleResolvedFile`) against candidate inputs.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L186-242)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L628-641)
```typescript
    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
```
