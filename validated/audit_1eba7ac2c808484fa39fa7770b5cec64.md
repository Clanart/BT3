## Title
Malformed conflict-hunk boundary detection in Copilot conflict resolution silently corrupts "ours" content when spliced back into the committed file - (`app/src/lib/copilot-conflict-context.ts`)

### Summary
GitHub Desktop's "Resolve with Copilot" feature parses conflict-marker blocks twice with two different, mutually inconsistent algorithms: `extractConflictHunks` (used to build the prompt/context sent to the model) and `reassembleResolvedFile` (used to splice the model's answer back into the working file). `extractConflictHunks` terminates the "ours" side of a hunk on the **first** line that merely looks like a separator (`^={7}$`), even if that line is legitimate file content rather than the real `=======` conflict marker. `reassembleResolvedFile`, by contrast, always treats the entire span from `<<<<<<<` to the corresponding `>>>>>>>` as one opaque block to be deleted and replaced by the model's answer. When a conflicted file's "ours" region happens to contain a line of seven or more `=` characters (a common pattern in banners, changelog dividers, Markdown/RST rules, etc.), the two parsers disagree about where "ours" ends and "theirs" begins. The model is fed truncated/incorrect "ours" and bloated "theirs" content, and whatever it returns is used to blindly overwrite the *entire* real marker span — permanently discarding the misclassified tail of the user's real content with no validation or warning.

### Finding Description
`extractConflictHunks` in [1](#0-0)  collects "ours" content and breaks out as soon as it sees **any** line matching `separatorMarker` (`^={7}$`) — it does not require that this be the sole complete marker, nor does it check for a subsequent well-formed `>>>>>>>`/`=======` pairing before committing to that boundary. That means an ordinary line inside the *real* "ours" content that happens to be exactly `=======` (or longer, e.g. `==============`) is misidentified as the diff separator.

`reassembleResolvedFile`, which actually performs the file rewrite, uses a completely independent scan in [2](#0-1) : it looks ahead for *any* separator-looking line (without breaking) and only stops at the real `>>>>>>>` closing marker, then unconditionally deletes everything between `<<<<<<<` and `>>>>>>>` and replaces it with `hunkResolutions[hunkIndex].resolvedContent`.

Because these two independent regex-based parsers use different termination rules for the same raw bytes, they can disagree on the internal structure of a conflict block even though they agree it is "one hunk." `extractConflictHunks` sends the model a corrupted view (real "ours" truncated at the fake separator; the remainder of the real "ours" content, the real separator, and the real "theirs" content are all merged into what the model believes is "theirs"). The model resolves based on that corrupted view, and its answer is spliced by `reassembleResolvedFile` over the *entire* true marker span — including the real "ours" tail that was never actually shown to the model as "ours." That content is silently lost unless the model happens to preserve it verbatim, which it has no reason to do since it was told it belongs to "theirs."

No code path cross-validates that the `oursContent`/`theirsContent` extracted by `extractConflictHunks` is consistent with the block boundaries `reassembleResolvedFile` will use, and no error is raised — the file is written via `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` in [3](#0-2)  and then `git add`-ed, with no diff review gate forcing the user to notice the loss.

### Impact Explanation
This is a silent corruption of what the user commits and pushes: real, previously-uncommitted local content ("ours") that was never actually in conflict can be dropped or altered without the user's knowledge, based purely on an attacker-influenced merge (e.g., a malicious incoming branch/PR the user merges) that happens to align a conflict against local content containing an innocuous run of `=` characters. Because the feature auto-stages (`git add`) the reassembled file, the corrupted content can flow directly into a commit and subsequent push before the user notices, matching the "silent corruption of what the user commits or pushes" impact class.

### Likelihood Explanation
Triggering the exact edge case requires: (1) the user has the Copilot conflict-resolution feature available and invokes it during a merge/rebase, and (2) the locally-modified "ours" region of a real conflict contains a line consisting solely of 7+ `=` characters before the true separator. Lines like `=======`, `==========`, or similar dividers are common in changelogs, banners, and documentation, so this is a plausible, not contrived, occurrence in real-world merges rather than a hand-crafted proof-of-concept requiring unnatural steps. It does not require local/physical access, elevated privileges, or leaked credentials — only a normal merge against attacker-influenced (or coincidentally-formatted) content.

### Recommendation
- Make `extractConflictHunks` robust: only treat a line as the diff separator when it is followed, before EOF, by a matching `>>>>>>>` closer (mirroring the look-ahead validation already used in `reassembleResolvedFile`), rather than terminating on the first `^={7}$` match.
- Add an invariant check that the boundaries used to build model context and the boundaries used for reassembly are derived from a single shared parse, not two independently maintained regex scanners.
- As defense in depth, diff the reassembled file against the original working-tree content before staging, and warn/block auto-staging if content outside the identified marker spans changed unexpectedly.

### Proof of Concept
1. Create a merge conflict where the "ours" side of the conflicted hunk contains a legitimate line of exactly `=======` before the real `=======` separator, e.g.:
```
<<<<<<< HEAD
some real code
=======
this looks like a real separator but is actually part of "ours" content
more real ours code
=======
their change
>>>>>>> feature
```
2. Invoke "Resolve with Copilot." `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:200-214`) stops collecting `oursContent` at the first `=======` line, so the model is shown:
   - `oursContent`: `"some real code"`
   - `theirsContent`: `"more real ours code\n=======\ntheir change"` (i.e., the real remaining ours content, the true separator text, and the real theirs content, all merged)
3. The model resolves based on this corrupted view (e.g., picks "theirs" as it understands it, or merges the pieces incorrectly).
4. `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:560-591`) deletes the entire original span from `<<<<<<< HEAD` to `>>>>>>> feature` and inserts only the model's answer — the line `more real ours code`, which the user's actual, intended local change, is not guaranteed to appear in the final file.
5. The result is auto-staged via `git add` in [4](#0-3) , so the corrupted content can be committed and pushed without the user manually reviewing every affected line.

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

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```

**File:** app/src/lib/stores/app-store.ts (L7262-7267)
```typescript
    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```
