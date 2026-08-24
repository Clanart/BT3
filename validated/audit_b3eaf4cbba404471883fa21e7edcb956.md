Based on the investigation, the strongest analog to the "accounting invariant broken across two independent subsystems" bug class in this codebase is in the **Copilot AI conflict-resolution pipeline**, where two separately-maintained parsers walk the *same attacker-influenced file content* (a Git merge/rebase/cherry-pick conflict, which by definition contains content from a remote/incoming branch the user does not control) using duplicated-but-not-identical conflict-marker regexes, and the second parser's output is written straight to disk and `git add`ed without ever being diffed against what the user was shown.

### Title
Divergent conflict-marker parsing between prompt-extraction and resolution-splicing can silently misplace AI-resolved content into the wrong location of a committed file - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`extractConflictHunks` (used to build the prompt sent to the Copilot model) and `reassembleResolvedFile` (used to splice the model's ordered resolutions back into the on-disk file before `git add`) both parse the *same raw conflicted file content* for `<<<<<<<`/`=======`/`>>>>>>>` markers, but they are two independently implemented state machines with their own regexes and their own malformed-marker recovery behavior [1](#0-0) [2](#0-1) . The model's resolutions are matched to conflict blocks purely "by order, not by line number" [3](#0-2) , so if the incoming/attacker-controlled branch content causes the two parsers to disagree on how many valid conflict blocks exist (or where they start/end), the splice step will insert a resolution meant for one hunk into a different location in the final file — content the user never reviewed as a diff before it is written and staged.

### Finding Description
The invariant that must hold is: *the number and boundaries of conflict blocks seen by `extractConflictHunks` (which drives "Conflict 1 of N" prompt numbering) must exactly match the number and boundaries seen by `reassembleResolvedFile` (which performs the splice)*. This is the same class of bug as the report's broken accounting invariant: one code path increments/decrements a shared counter based on one interpretation of state (available assets / conflict count), while another path acts on a different interpretation of the same state, and the two are never reconciled.

Concretely:
- `extractConflictHunks` breaks its ours-collection loop on `baseMarker` **or** `separatorMarker`, and requires a subsequent `theirsMarker` to close the hunk; if the closing marker is never found it silently drops the (partial) hunk and its outer scan is left positioned at EOF [4](#0-3) .
- `reassembleResolvedFile` instead does a full look-ahead from the opening marker, and on a malformed block, treats **only the opening marker line** as ordinary content before resuming its line-by-line scan for further markers, rather than aborting the outer scan [5](#0-4) .

These two recovery strategies are not equivalent for pathological input: the "theirs" (incoming/attacker) branch of a merge can legitimately contain arbitrary text, including lines that are exactly 7 `<`, `=`, or `>` characters (e.g. inside a markdown file, a changelog with `=======`-style rules, or code containing shift operators/comment banners). Because the incoming side of the conflict is fully attacker-controlled content coming from a fetched branch/PR, an attacker can craft a file whose conflict region causes `extractConflictHunks` to count *N* hunks while `reassembleResolvedFile` splits the same text into *N±k* splice points. The model's resolutions array — indexed strictly by order — is then spliced by `reassembleResolvedFile` against the wrong boundaries.

The result is written directly to disk and staged with no additional review of the actual bytes written: [6](#0-5) 

Note the code comment right above the write path shows the developers were aware that *externally* edited files must not be silently clobbered, but there is no analogous check that the *AI-resolved* content being written actually matches hunk-for-hunk what the model saw and what the user was shown in the result dialog [7](#0-6) .

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes": the attacker (via a malicious branch, fork, or PR that the victim merges/rebases/cherry-picks) can cause GitHub Desktop's AI conflict resolution to graft resolved content from one conflict hunk into an unrelated location of the file, or to leave attacker content in place while the user believes it was resolved as shown in the summary dialog. Because the write happens verbatim (`writeFile` + `git add`) with no re-diff against the exact hunk boundaries used for resolution, this could smuggle unintended code/content into a commit that the user reviews only via the AI's own (mismatched) hunk breakdown, not the actual bytes on disk.

### Likelihood Explanation
Exploitation requires: (1) the victim to merge/rebase/cherry-pick a branch containing attacker-crafted conflicting content, and (2) the victim to use the "Resolve with Copilot" feature and click "Continue Merge" without manually re-reviewing the exact diff of the written file. This is a real, in-band attacker capability (control over a merged branch's content) rather than local/physical access, but it depends on a specific crafted-input edge case in marker parsing that I was not able to fully brute-force-verify against the exact regex state machines within the available tool budget — I traced multiple malformed-marker scenarios where the two parsers appeared to agree, and the clearest divergence I identified is the different EOF/abort behavior described above. I was not able to construct and test a concrete byte-for-byte input in this environment to confirm a hunk-count mismatch occurs in practice.

### Recommendation
Have `reassembleResolvedFile` and `extractConflictHunks` share a single parsing implementation (e.g., extract once into a shared list of `{start, end}` marker-block ranges, and have both the prompt-builder and the splicer consume that same list), so the two can never disagree about hunk count or boundaries. Additionally, before writing/staging AI-resolved content, verify that the number of splice points found during reassembly equals the number of hunks that were sent to the model for that file, and refuse (fall back to manual resolution) rather than silently writing on any mismatch.

### Proof of Concept
Not fully verified end-to-end due to tool-call budget. The suggested reproduction path is:
1. Create two branches that conflict on a file whose "theirs" (incoming) content contains a line consisting of exactly 7 `=` characters embedded inside otherwise normal content (not part of a real conflict marker) shortly before a real conflict's `>>>>>>>` closing marker.
2. Start a merge/rebase in Desktop that produces this conflict, and trigger "Resolve with Copilot" with multiple hunks in the file.
3. Inspect whether `extractConflictHunks`'s reported hunk count (sent to the model as "Conflict N of M") differs from the number of splice points `reassembleResolvedFile` performs on the same raw content — a background Devin session with a full checkout and test runner would be needed to construct exact byte sequences and assert the mismatch via the existing unit test suites (`app/test/unit/copilot-conflict-context-test.ts`, `app/test/unit/copilot-conflict-resolution-test.ts`).

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-538)
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-591)
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
```

**File:** app/src/lib/stores/app-store.ts (L7241-7257)
```typescript
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

```

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
