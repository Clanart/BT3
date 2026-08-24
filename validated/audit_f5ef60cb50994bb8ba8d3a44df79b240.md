## Title
Order-based (not marker-verified) hunk matching in Copilot conflict reassembly can splice AI-resolved content into the wrong location, silently corrupting the committed file - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`reassembleResolvedFile()` walks a conflicted working-tree file and replaces each detected `<<<<<<<...=======...>>>>>>>` block with the corresponding entry from `hunkResolutions`, matched purely **by array index/order**, not by any content or position identifier [1](#0-0) . Malformed marker blocks (missing separator or missing closing marker) are explicitly *not* treated as conflict hunks and are copied through unchanged, skipping the hunk counter [2](#0-1) . Because this counting logic depends entirely on the textual shape of the file at reassembly time, any mismatch between what the model was shown/asked to resolve and what this function counts as a "real" conflict block causes hunk resolutions to be spliced into the wrong marker block — exactly the same class of bug as the reported issue: a value (here, the hunk index used to select which resolution content to apply) is not kept consistent with the actual, narrowed/clamped state of the underlying data (here, the real positions of conflict blocks in the file).

### Finding Description
The reassembly function has no invariant check that `hunkResolutions.length` equals the number of well-formed conflict blocks it found, nor any check that resolution `i` actually corresponds to conflict block `i` in the file. It only requires `hunkIndex < hunkResolutions.length` to consume an entry [3](#0-2) . This is fed by `reassembleResolutions()`, which fetches `ctx.rawContent` (the raw on-disk file with markers) and blindly zips it with the model's `raw.hunks` array from `IRawFileResolution` [4](#0-3) .

An attacker who controls a branch/PR being merged (i.e., a git remote object under attacker control, per the accepted threat model) can craft a file so that it contains text that looks like a conflict marker but is malformed as parsed by this specific regex-based scanner (e.g. a `<<<<<<<` line followed by a `=======` but with the code intentionally breaking the greedy-search for `>>>>>>>` before EOF, or vice versa) while genuine conflicts exist elsewhere in the same file. Because the AI model that generates `hunks` looks at the file/conflict data assembled elsewhere in the pipeline (`copilot-conflict-context.ts`'s `extractConflictHunks`, a *different*, more permissive/differently-behaving line scanner used to build the prompt — as shown by its own dedicated test suite covering CRLF handling and marker bleed-through [5](#0-4) ), the number/order of "hunks" the model reasons about is not guaranteed to be identical to the number/order `reassembleResolvedFile` finds when actually splicing content back into the file. When these two independent hunk-counting passes disagree (attacker-crafted marker-like content is the simplest way to force a disagreement), the model's resolution for conflict N gets written into conflict block M ≠ N — the app then does `await writeFile(absolutePath, resolution.resolvedContent, 'utf8')` and stages the result [6](#0-5) .

The only existing guard, `hasUnresolvedConflicts`, merely skips files a user has already fully resolved externally; it does not detect or prevent a hunk-count mismatch within a still-conflicted file [7](#0-6) .

### Impact Explanation
The user clicks "Continue Merge/Rebase/Cherry-pick", trusting the Copilot-resolved diff they were shown in `CopilotConflictsChanges`/`copilot-conflicts-dialog.tsx`. If hunk indices are shifted due to attacker-crafted marker-like content, the file actually written and committed can differ from what was previewed and approved, silently baking incorrect/attacker-influenced code (e.g., code from the wrong side of the merge, or reasoning intended for a different hunk) into the resulting commit that gets pushed. This is a silent corruption of what the user commits/pushes — the exact impact class called out as valid.

### Likelihood Explanation
This requires the victim repository to be merging/rebasing against a branch or PR containing attacker-authored content with conflict-marker-like text engineered to desync the two hunk-parsing passes (`extractConflictHunks` for prompting vs. the marker scanner inside `reassembleResolvedFile`), and requires the user to opt into the Copilot conflict-resolution flow. It does not require local/physical access, admin rights, or any credential — only an untrusted git ref being merged, matching the medium-likelihood profile of the original report (not every conflict resolution reaches this edge case, only ones with malformed/ambiguous marker sequences).

### Recommendation
Do not match hunk resolutions to conflict blocks by array order alone. Use a single, shared hunk-extraction routine for both the AI prompt (`copilot-conflict-context.ts`) and the reassembly step (`copilot-conflict-resolution.ts`), and have `reassembleResolvedFile` assert that the number of well-formed conflict blocks it finds exactly equals `hunkResolutions.length` before splicing — failing closed (falling back to manual resolution) rather than silently applying a shifted mapping. Consider embedding a stable identifier (e.g., a hash or line-range) per hunk in the request/response so reassembly can validate correspondence rather than relying on ordinal position.

### Proof of Concept
Conceptual PoC (not runtime-verified in this session):
1. Attacker prepares a branch that, when merged, produces a conflicted file containing:
   - A genuine, well-formed conflict block (block A).
   - Immediately after, a snippet of text that begins with `<<<<<<< something` (e.g. inside a code comment or string literal) followed by a `=======`-looking line but no matching `>>>>>>>` before EOF — malformed per `reassemblyOursMarker`/`reassemblySeparatorMarker`/`reassemblyTheirsMarker` regexes [8](#0-7) .
   - A second genuine conflict block (block B) further down.
2. `extractConflictHunks` (used to build the Copilot prompt) may parse this differently (e.g., treat the malformed block as a real conflict, or merge/split blocks differently) than `reassembleResolvedFile`'s scanner, which will skip the malformed block and treat block B as `hunkIndex === 1` instead of `2`.
3. The model returns `hunks = [resolutionForA, resolutionForMalformedBlock, resolutionForB]` (3 entries) based on its own view of the file.
4. `reassembleResolvedFile` only finds 2 real conflict blocks (A and B), so it applies `hunks[0]` to A and `hunks[1]` (intended for the malformed/non-existent block) to B, dropping `hunks[2]` (the real resolution intended for B).
5. The file is written to disk and staged via `git add` without further verification [9](#0-8) , resulting in block B being committed with content that was never actually reviewed/intended for that location.

Note: this could not be executed against a live build in this session (no filesystem/terminal access); the analysis above is based purely on static reading of `app/src/lib/copilot-conflict-resolution.ts`, `app/src/lib/copilot-conflict-context.ts`, and `app/src/lib/stores/app-store.ts`. Confirming the exact divergence between `extractConflictHunks` and `reassembleResolvedFile`'s marker regexes would require running both against a crafted fixture, which a Devin session with repo/terminal access could verify.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L529-536)
```typescript
 * Reassemble a fully resolved file by splicing per-hunk resolutions into
 * the original file content (which still has conflict markers on disk).
 *
 * Walks the original file line-by-line. Non-conflicted lines are copied
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-641)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

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

**File:** app/test/unit/copilot-conflict-context-test.ts (L52-99)
```typescript
describe('copilot-conflict-context', () => {
  describe('extractConflictHunks', () => {
    it('handles CRLF line endings (Windows)', () => {
      const content = [
        'line before',
        '<<<<<<< HEAD',
        'our change',
        '=======',
        'their change',
        '>>>>>>> feature',
        'line after',
      ].join('\r\n')

      const hunks = extractConflictHunks(content)

      assert.equal(hunks.length, 1)
      assert.equal(hunks[0].oursContent, 'our change')
      assert.equal(hunks[0].theirsContent, 'their change')
      assert.equal(hunks[0].baseContent, null)
    })

    it('does not bleed conflict markers into context lines', () => {
      const content = [
        'start',
        '<<<<<<< HEAD',
        'ours-1',
        '=======',
        'theirs-1',
        '>>>>>>> feature',
        'middle',
        '<<<<<<< HEAD',
        'ours-2',
        '=======',
        'theirs-2',
        '>>>>>>> feature',
        'end',
      ].join('\n')

      const hunks = extractConflictHunks(content, 5)

      assert.equal(hunks.length, 2)
      // First hunk contextAfter should stop before the next <<<<<<< marker
      assert.equal(hunks[0].contextAfter, 'middle')
      assert.ok(!hunks[0].contextAfter.includes('<<<<<<<'))
      // Second hunk contextBefore should stop after the previous >>>>>>> marker
      assert.equal(hunks[1].contextBefore, 'middle')
      assert.ok(!hunks[1].contextBefore.includes('>>>>>>>'))
    })
```

**File:** app/src/lib/stores/app-store.ts (L7241-7256)
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

**File:** app/src/lib/stores/app-store.ts (L7258-7267)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
```
