## Analysis Summary

The report's bug class is: a validation/parsing step relies on an *assumed* invariant about where a value boundary lives, that invariant silently breaks under a specific (but plausible) data state, and existing checks don't catch it because they validate the wrong thing (count/existence, not content correctness) — leading to loss/corruption of user-owned assets.

The closest real Desktop analog is in the Copilot conflict-resolution pipeline, which parses raw conflict-marker text from a file that can originate from an untrusted branch/PR being merged, and later splices AI-generated content back into the file that gets committed.

### Title
Malformed conflict-marker interior content causes Copilot conflict resolution to silently misattribute "ours" vs "theirs" text and corrupt the resulting commit - (File: app/src/lib/copilot-conflict-context.ts)

### Summary
`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` splits a conflict block into `oursContent` / `theirsContent` by scanning for the *first* line that matches the separator regex (`={7}`), while `reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` only needs to find the *outer* `<<<<<<<` … `>>>>>>>` span to know where to splice the model's resolved text back into the file. These two functions do not agree on how the *interior* of a conflict block is structured, and the mismatch is never surfaced to `validateResolutionPaths`, which only checks file/path presence and per-file hunk *counts* — not the correctness of the ours/theirs partition inside each hunk.

### Finding Description
`extractConflictHunks` collects "ours" lines until it hits the first line matching `separatorMarker` (`/^={7}$/`) or `baseMarker`: [1](#0-0) 

It then collects "theirs" lines purely until it hits `theirsMarker` (`/^>{7}(?:\s|$)/`), without re-checking for a stray separator line: [2](#0-1) 

If the *legitimate* content on either side of a real conflict happens to contain a standalone line of exactly seven (or more) `=` characters — a Markdown Setext underline, an RST/changelog divider, an ASCII banner, all extremely common and requiring no attacker sophistication — this line is misidentified as the "ours"/"theirs" boundary. The real ours-side content that follows the decoy line, plus the real separator line itself, get folded into `theirsContent` instead of `oursContent`. The model (Copilot) is then prompted with content that misattributes what belongs to "ours" vs "theirs".

Meanwhile, `reassembleResolvedFile` re-scans the same raw file but only needs the *outer* boundary — the first `<<<<<<<` and the first subsequent `>>>>>>>`, with any `=======` in between satisfying `hasSeparator`: [3](#0-2) 

Because this only needs one separator occurrence and the first closing marker, it identifies the *same overall block* as `extractConflictHunks` despite the interior being parsed differently. The number of hunks found by each function therefore stays consistent, so `validateResolutionPaths` — which only checks path sets, duplicates, and hunk *counts* — passes without error: [4](#0-3) 

The result: the model resolves the conflict based on content it was told was "theirs" that was actually part of "ours" (or vice versa), and its `resolvedContent` is spliced wholesale into the correct block boundaries by `reassembleResolvedFile`, then written to disk and staged automatically: [5](#0-4) 

No error, warning, or hunk-count mismatch occurs anywhere in this path — the corruption is silent.

### Impact Explanation
This results in silent corruption of what the user commits: content that the user's own branch actually contributed can be attributed to (and potentially discarded or merged incorrectly with) the incoming branch's changes, and the reverse. Since Copilot's resolution is auto-staged and the summary/diff surfaced to the user does not expose the internal ours/theirs misattribution, a user could unknowingly commit and push a merge that silently drops or garbles their own prior work, satisfying the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
The triggering condition — a bare line of `=======` (or more `=` characters) inside legitimate file content on one side of a real merge conflict — is common in Markdown, RST, and changelog-style files, and requires no deliberate attacker action; it can occur from an ordinary PR/branch. It's also trivially plannable by an adversarial contributor whose branch is merged/rebased/cherry-picked by a victim who then clicks "Resolve with Copilot." However, it only manifests when: (1) the conflicting file actually contains such a divider line, and (2) the user opts into Copilot-based conflict resolution rather than resolving manually, which somewhat narrows real-world frequency.

### Recommendation
Make `extractConflictHunks`'s interior split symmetric with `reassembleResolvedFile`'s outer-boundary detection: locate the outer `<<<<<<<` … `>>>>>>>` span first, then only accept the LAST unmatched `=======` line before the closing marker as the ours/theirs boundary (or otherwise disambiguate `=======` divider lines from the true separator, e.g., by requiring markers to be conflict-generated by git and validating against `git diff --check`/`ls-files -u` output rather than freeform regex scanning of arbitrary file text). Additionally, `validateResolutionPaths` should be strengthened to verify structural round-trip correctness (e.g., that the concatenation of ours+separator+theirs reconstructed from the parsed hunk matches the original raw block) rather than only checking hunk counts.

### Proof of Concept
1. Create a merge conflict in a Markdown file where the "ours" side content ends with a Setext-style heading underline, e.g.:
```
<<<<<<< HEAD
Our Heading
=======
More of our real paragraph text
=======
Their replacement paragraph
>>>>>>> feature
```
2. Trigger the conflict via a normal merge/rebase in Desktop and click "Resolve with Copilot."
3. `extractConflictHunks` stops collecting `oursContent` at the first `=======` (`Our Heading`), and folds `More of our real paragraph text\n=======\nTheir replacement paragraph` into `theirsContent`.
4. The model is prompted with wrong `oursContent`/`theirsContent`, and `validateResolutionPaths` accepts the single-hunk response since the hunk count (1) is unchanged.
5. `reassembleResolvedFile` splices the model's `resolvedContent` into the whole `<<<<<<<`…`>>>>>>>` span, and `_applyCopilotConflictResolutions` writes and `git add`s the result — silently committing content derived from a misattributed ours/theirs split, with no error surfaced to the user.

### Citations

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

**File:** app/src/lib/copilot-conflict-context.ts (L228-237)
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L560-591)
```typescript
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
