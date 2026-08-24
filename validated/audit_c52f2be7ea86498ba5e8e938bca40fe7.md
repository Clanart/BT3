## Title
AI conflict-resolution reassembly can silently leave raw conflict markers (or misapply resolutions) when repo content contains marker-lookalike text — (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
GitHub Desktop's Copilot-powered merge-conflict resolver extracts conflict hunks with one line-scanning algorithm (`extractConflictHunks`) and later splices the model's resolutions back into the file with a *different*, independently-written line-scanning algorithm (`reassembleResolvedFile`). The two algorithms disagree on what counts as a "closing" conflict marker, so a file whose *legitimate content* happens to contain a line matching the 7-character `>>>>>>>` pattern (very plausible in docs/tutorials about Git, or in code with deeply nested blockquotes/templates) can make the reassembly step treat a real, correctly-extracted conflict block as malformed. The result is silently corrupted file content — raw `<<<<<<<`/`=======`/`>>>>>>>` markers left in the "resolved" file, and/or a resolution meant for one conflict being spliced into a different conflict — that the user can then commit and push without any warning.

### Finding Description
`extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-279`) collects the "ours" side of a hunk by scanning forward and stopping only on `baseMarker` or `separatorMarker`: [1](#0-0) 
It never checks for `theirsMarker` while accumulating ours-content, so any line inside the ours block that happens to match `^>{7}(?:\s|$)` is simply included as content, and the hunk is extracted correctly.

`reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) re-parses the same raw file independently, using a look-ahead loop that treats the *first* line matching `reassemblyTheirsMarker` as the closing marker, regardless of whether a `=======` separator has been seen yet: [2](#0-1) 

If the "ours" content of a real conflict contains a marker-lookalike line before the true `=======` separator, this loop's `closingIndex` gets set prematurely, `hasSeparator` is still `false`, and the block is classified as malformed: [3](#0-2) 
On this path the function does `i++` (not `closingIndex + 1`) and pushes only the `<<<<<<<` line through as plain text — it does **not** skip past the rest of the block. The subsequent lines (the real `=======`, "theirs" content, and the true `>>>>>>>`) are not conflict markers as far as the outer loop is concerned (only `reassemblyOursMarker` is checked at the top of the loop), so they are copied through verbatim as literal file content. Critically, `hunkIndex` is only incremented on the well-formed path, so the resolution the model produced for this hunk is never consumed here — it will instead be spliced into whatever the *next* real conflict block in the file is, or dropped entirely if there is none.

There is no invariant check at the end of `reassembleResolvedFile` asserting that `hunkIndex === hunkResolutions.length`, and `validateResolutionPaths` (`app/src/lib/copilot-conflict-resolution.ts:473-521`) only compares hunk *counts* between what `extractConflictHunks` found and what the model returned — it cannot detect that the two independent regex-based scanners disagree about which lines are markers within the same file. The mismatch is entirely silent.

### Impact Explanation
An attacker who controls content in a cloned/fetched repository (a common, unprivileged capability — e.g. contributing a file, or the victim cloning an attacker-controlled repo) can craft a file (for example, documentation about how to resolve Git conflicts, which conventionally shows literal `<<<<<<<`/`=======`/`>>>>>>>` example text, or content with 7+ leading `>` characters) such that a subsequent, entirely normal merge/rebase conflict on that file causes GitHub Desktop's AI conflict-resolution feature to:
- leave literal, unresolved conflict-marker text embedded in the committed file (potential build breakage, or worse, silently broken logic depending on language), and/or
- apply the model's intended resolution for one hunk to an unrelated hunk in the file, corrupting code the user believes was correctly merged.

This is a silent corruption of what the user commits and pushes, requires no special privileges, and does not require any unusual user action beyond using the built-in Copilot conflict resolution feature and then committing as normal.

### Likelihood Explanation
The trigger condition (a source line whose first 7+ characters are `>`) is more plausible than it first appears: Markdown files frequently contain literal example conflict markers when documenting Git workflows (CONTRIBUTING.md, tutorials, wikis), and deeply nested blockquotes or ASCII-art dividers can also match. Any repository with such content that later develops a genuine merge conflict in that same region will trigger the divergence between `extractConflictHunks` and `reassembleResolvedFile`. Because it only requires normal repository content plus normal use of the AI conflict-resolution feature, likelihood is moderate, and detection by the user is unlikely since the tool provides no warning that a hunk failed to reassemble.

### Recommendation
Unify the marker-scanning logic used by `extractConflictHunks` and `reassembleResolvedFile` into a single shared parser so both stages agree on hunk boundaries by construction, rather than maintaining two independently-hand-rolled regex scanners over the same file. Additionally, add a hard invariant check at the end of `reassembleResolvedFile` that `hunkIndex === hunkResolutions.length` (and that no conflict block was treated as malformed when a resolution was expected for it), throwing a `CopilotValidationError` instead of silently emitting corrupted content.

### Proof of Concept
1. Craft a repository file `NOTES.md` containing, among other content, seven or more literal `>` characters at the start of a line inside a region that will become the "ours" side of a future conflict, e.g.:
   ```
   Example of a conflict:
   >>>>>>> old-example
   ```
2. Set up two branches that both modify the surrounding lines of `NOTES.md`, causing a real merge conflict whose "ours" hunk contains the line above before the real `=======`/`>>>>>>>` markers.
3. In GitHub Desktop, trigger the merge and use "Resolve conflicts with Copilot."
4. `extractConflictHunks` correctly extracts one hunk and sends it to the model; the model returns one resolution.
5. `reassembleResolvedFile` mis-detects the block as malformed (because it hits the lookalike `>>>>>>> old-example` line before the real separator), leaves the true `<<<<<<<`/`=======`/`>>>>>>>` markers as literal text in the output file, and does not consume the model's resolution for this hunk (shifting it to any later hunk, or dropping it).
6. The user is shown the file as "resolved" and commits/pushes it, silently including raw conflict-marker text (and/or a misapplied resolution) in the repository history.

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
