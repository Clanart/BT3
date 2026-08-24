## Title
Order-based (not identity-based) splicing of Copilot conflict resolutions can silently corrupt unrelated file content - (File: `app/src/lib/copilot-conflict-resolution.ts`)

## Summary
`reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` reconstructs a resolved file by scanning the on-disk raw content for conflict-marker blocks (`<<<<<<<` / `=======` / `>>>>>>>`) using simple regexes, and splices in the model's per-hunk resolution **by positional order**, not by any binding to the specific hunk the model actually saw: [1](#0-0) 

The matching logic walks the file top-to-bottom and, each time it finds what looks like a well-formed marker block, consumes the next entry from `hunkResolutions[hunkIndex++]`: [2](#0-1) 

This is directly analogous to the reported bug class: the code assumes a quantity (the Nth marker block found by regex scan) is equivalent to a different quantity (the Nth hunk context that was actually sent to, and resolved by, the model), without verifying they still correspond 1:1 by the time the substitution happens.

## Finding Description
The invariant `reassembleResolvedFile` depends on is: *"the sequence of conflict-marker blocks found by regex-scanning `rawContent` at reassembly time is identical, in count and order, to the sequence of hunks that were extracted from that same file and sent to the Copilot model."*

That invariant is not verified anywhere in `reassembleResolutions`, which simply looks up file content by path and calls `reassembleResolvedFile(ctx.rawContent, raw.hunks)`: [3](#0-2) 

The repository content that produces `rawContent` is fully attacker-controlled: it comes from a merge/rebase/cherry-pick against a branch, remote, or PR the user is merging — i.e., exactly the "attacker controls a cloned/fetched repository" scenario allowed by the task's valid-impact definition. Because the marker-detection regexes (`reassemblyOursMarker`, `reassemblySeparatorMarker`, `reassemblyTheirsMarker`) only look at line-start patterns (`^<{7}`, `^={7}$`, `^>{7}`) with no correlation to actual git conflict-hunk boundaries or IDs, any line in the file that incidentally matches those patterns — for example inline documentation/test fixtures that literally contain example conflict markers, or a genuine conflict hunk whose "well-formedness" check (`hasSeparator`/`closingIndex`) is satisfied/unsatisfied in an attacker-crafted way — shifts `hunkIndex` relative to what the code building `raw.hunks` and the model actually reasoned about.

The code's own comment acknowledges the risk explicitly ("matched by order, not by line number"), and the project's changelog shows this exact area has already produced a real, shipped data-loss bug: [4](#0-3) 

`[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349`

That fix did not change the fundamental order-based matching strategy — it remains structurally the same mechanism, so any future desync between "blocks found in `rawContent`" and "hunks resolved by the model" (extra/missing/malformed marker blocks introduced by the attacker-controlled merge input, or the model returning a hunk count that doesn't match what was actually extracted) reproduces the same class of silent corruption.

## Impact Explanation
When `hunkIndex` desyncs, `resultLines.push(...resolved.split(...))` inserts the resolved text for hunk *N* into the position of block *N* in the file, which may not be the block the model actually resolved. The result is written straight to disk and staged without further validation: [5](#0-4) 

This is a silent corruption of what the user commits — content unrelated to the intended conflict resolution can be overwritten or a resolution meant for one hunk can be applied at the wrong location, and the user is shown a diff/summary generated from the same flawed reassembly, so the UI would not reliably flag the mismatch before the write. Given the explicit precedent (#22349) of “file content outside conflicted regions was overwritten,” this qualifies as the required impact: silent corruption of what the user commits or pushes.

## Likelihood Explanation
Likelihood is Medium-to-Low: it requires the user to opt into "Resolve merge conflicts with Copilot" and to be merging/rebasing against attacker-influenced content (a malicious PR branch, remote, or forked contribution) that either contains conflict-marker-like text outside real conflicts or produces a conflict layout that induces a model/regex mismatch. It does not require local/physical access, admin rights, leaked credentials, or unnatural user steps beyond the normal act of resolving conflicts with an AI assistant feature that Desktop ships and promotes.

## Recommendation
Do not rely on positional order to bind a model-returned hunk resolution to a specific on-disk conflict block. Instead:
- Assign each extracted conflict hunk a stable identifier (e.g., its byte/line offset range or an explicit hunk index) when building the prompt context, and require the model's response to echo that identifier back for each resolved hunk.
- In `reassembleResolvedFile`/`reassembleResolutions`, re-derive the conflict blocks from `rawContent` independently and assert that the count and identifiers match `hunkResolutions` exactly before substitution; abort (fall back to manual resolution) on any mismatch instead of silently proceeding.
- Add defensive validation that rejects a resolution result if the number of well-formed marker blocks found does not exactly equal `raw.hunks.length`.

## Proof of Concept
Conceptual reproduction (cannot be executed here, but demonstrates the broken invariant):
1. Create a merge with two genuine conflicts in `file.ts`.
2. Have one hunk's "theirs" side (attacker-controlled, e.g. content pulled from a malicious PR branch) contain a code/documentation snippet that itself embeds literal lines starting with `<<<<<<<`, `=======`, `>>>>>>>` but not forming a complete well-formed block (fails the `hasSeparator && closingIndex !== -1` check) — this text is copied through verbatim by the "malformed marker" path, per lines 574-578 in `copilot-conflict-resolution.ts`.
3. When the model resolves the two genuine hunks, `parseCopilotConflictResolution` returns two hunk resolutions in file order, but the reassembly regex scan may encounter additional partially-matching marker lines from step 2 depending on ordering/content, shifting which conflict block in `rawContent` consumes `hunkResolutions[0]` vs `hunkResolutions[1]`.
4. `_applyCopilotConflictResolutions` writes the reassembled (now mismatched) content directly to disk and `git add`s it, so the corrupted result is committed without further confirmation of hunk-to-content correctness.

This mirrors the reported bug's core flaw: an unstated assumption that "amount/position received == amount/position expected," used downstream without verification, leading to a wrong but successfully-processed result — in this case, silently corrupted committed file content instead of a failed token transfer.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-547)
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
 *
 * A `<<<<<<<` line that is not followed by both a `=======` separator and
 * a closing `>>>>>>>` before EOF is treated as regular file content (not a
 * conflict block) and copied through unchanged to avoid data loss from
 * malformed or stray markers.
 *
 * @param rawContent - The full file content on disk, including conflict markers
 * @param hunkResolutions - Per-hunk resolved content, in the order they appear in the file
 * @returns The reassembled file with all conflicts resolved
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

**File:** changelog.json (L94-96)
```json
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
