## Title
Attacker-controlled marker-like text can hijack Copilot conflict-resolution hunk boundaries, silently merging/hiding a real conflict — (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The external HyperdriveLP report describes a case where a corrective calculation (`overestimatedProceeds`) is computed but not actually applied before the value is used to pay out the user, so a stale/incorrect amount silently reaches the payout path even though the "safe" value existed. The Desktop analog is in the Copilot-assisted merge-conflict resolution feature: the hunk-boundary scanner that both (1) builds the AI prompt and (2) splices the AI's answer back into the file on disk uses a naive, unpaired, line-anchored regex match for `<<<<<<<`/`=======`/`>>>>>>>` without verifying that a matched "start" marker is not itself nested inside — or interleaved with — a subsequent, unrelated real conflict block. An attacker who controls file content merged/rebased into the user's repository can plant a single line that merely *looks like* a conflict-start marker; this causes the parser to swallow a real, adjacent conflict into the same bogus "hunk," collapsing two conflicts into one opaque region whose final content is decided entirely by the model's guess rather than a deterministic, user-reviewable resolution of the real conflict.

### Finding Description
`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` scans a file line-by-line for a line matching the `oursMarker` regex (7 `<` chars at line start) to begin a hunk, then greedily consumes lines until it hits the first `separatorMarker`/`baseMarker`, then consumes lines until the first `theirsMarker`, closing the hunk: [1](#0-0) 

Critically, while collecting the "ours" and "theirs" sections it only checks for `separatorMarker`/`baseMarker`/`theirsMarker` — it never checks whether a subsequent line is itself a *new* `oursMarker` line. If a file contains a stray line that matches the `oursMarker` pattern (7 `<` characters followed by whitespace or end-of-line) anywhere before a legitimate, nearby conflict block, the scanner will start a "hunk" there and keep consuming forward lines — including the real conflict's ours-content, its real `=======` separator, and its real `theirs` content — until it finds the first `>>>>>>>` line, which will be the *real* hunk's closing marker. The result is a single bogus hunk whose `oursContent`/`theirsContent` silently contain the real conflict's markers/content as plain text, and the genuinely separate second conflict is never surfaced as its own hunk.

The exact same asymmetric, first-match, non-nested scanning logic is reused when the model's answer is spliced back into the file on disk in `reassembleResolvedFile`: [2](#0-1) 

Because both the context-builder and the reassembly-writer independently derive the *same* (wrong) hunk boundaries from the same flawed algorithm, the hunk *count* stays consistent between the two passes, so `validateResolutionPaths` — which only checks that the number of returned hunks per file matches the number of expected hunks — does not detect anything wrong: [3](#0-2) 

The write path itself performs path-traversal checks and skips files a user already resolved externally, but has no way to know that the "hunk" it is writing actually represents two merged, unrelated conflicts: [4](#0-3) 

The UI dialog that lets the user review/override Copilot's suggestion per file renders one entry per *detected* hunk (`file.hunks.length`), so a real second conflict that got silently absorbed into the first is never shown to the user as a distinct item to review or override: [5](#0-4) 

### Impact Explanation
This results in **silent corruption of what the user commits/pushes**, one of the explicitly valid impact categories. The user clicks "Resolve with Copilot" and "Continue Merge" expecting each conflict marker block to be independently resolved and reviewable; instead, an attacker-crafted line elsewhere in the merged/rebased content can cause a real, unrelated conflict to be absorbed into a bogus region whose resolution is decided by whatever the model infers from a garbled prompt (the two conflicts' `ours`/`theirs` sections concatenated together as one "ours"/"theirs" block), and that content is written straight to disk and `git add`-ed without the user ever seeing "Conflict 2" as a separate, reviewable item. This can cause incorrect code (e.g., silently dropped changes from one side of a real conflict) to be committed and later pushed, without the same visibility/override controls the feature otherwise provides.

### Likelihood Explanation
The attacker only needs to get a single line matching `^<{7}(?:\s|$)` (e.g. `<<<<<<< example`) into a file on a branch, PR, or upstream ref that the victim later merges/rebases/cherry-picks in Desktop with Copilot conflict resolution enabled, positioned before a real, unrelated conflict in the same file (in the same commit range being merged). This is plausible in documentation/tutorial files about git, code containing example diff/patch text, or test fixtures — all of which are ordinary, unprivileged file content an external contributor can introduce via a normal PR/branch. No special git tooling, admin rights, or local access are required; it is triggered purely by the victim using Desktop's built-in Copilot conflict-resolution feature on attacker-supplied repository content.

### Recommendation
Harden the marker scanner in both `extractConflictHunks` and `reassembleResolvedFile` so that while scanning forward for a separator/closing marker, encountering a *new* `oursMarker` line before a matching `separatorMarker`/`theirsMarker` is treated as proof the initial marker was not a real, well-formed conflict start (mirroring the existing "malformed hunk, no closing marker" handling), rather than silently absorbing it as literal content. Additionally, `validateResolutionPaths` should not rely solely on hunk *count* equality — consider embedding a lightweight fingerprint (e.g. a hash of `oursContent`/`theirsContent`) per hunk in the context sent to and expected back from the model so that boundary drift between the context-build pass and the reassembly pass can be detected and rejected rather than silently accepted.

### Proof of Concept
1. Prepare a file `notes.md` with a benign-looking documentation snippet followed shortly after by a real merge conflict, e.g.:
```
Example git output:
<<<<<<< example-not-a-real-conflict
some line
context line
<<<<<<< HEAD
real ours content
=======
real theirs content
>>>>>>> feature-branch
after
```
2. Merge/rebase a branch that produces exactly this on-disk state for `notes.md` (the `<<<<<<< HEAD ... ======= ... >>>>>>> feature-branch` block is Desktop's own real conflict marker for an actually-conflicting hunk; the first `<<<<<<< example-not-a-real-conflict` line is attacker-authored plain text already present in the file before the merge).
3. In Desktop, invoke "Resolve with Copilot" on this conflict.
4. Observe `extractConflictHunks` reports only 1 hunk for the file (verifiable via the same logic as `app/test/unit/copilot-conflict-context-test.ts`'s existing marker tests), with `oursContent` containing the literal text `context line\n<<<<<<< HEAD\nreal ours content` and `theirsContent` = `real theirs content` — i.e., the real conflict's structure has been folded into one bogus hunk.
5. After the model responds and `_applyCopilotConflictResolutions` writes the file, the on-disk content for that whole span is replaced by the model's single guess, and the Copilot conflicts dialog shows only "Conflict 1 of 1" for this file — the real second conflict was never surfaced to the user for independent review/override.

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

**File:** app/src/lib/copilot-conflict-context.ts (L560-563)
```typescript
    for (let i = 0; i < file.hunks.length; i++) {
      const hunk = file.hunks[i]
      parts.push(`### Conflict ${i + 1} of ${file.hunks.length}`)
      parts.push('')
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
