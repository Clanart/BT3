## Analysis

The Beranames report's core flaw is **incomplete payload validation**: a struct has more fields than the ones actually covered by the integrity check, so an attacker can hold onto a validated artifact and freely mutate the uncovered fields, corrupting the outcome while the check still passes.

The closest analog in GitHub Desktop's code is in the Copilot-based merge-conflict auto-resolution feature. The LLM output (an attacker-influenceable artifact, since its input is the literal content of the conflicting hunks pulled from a fetched/merged branch) is validated by `parseCopilotConflictResolution`, but that validation only checks the *shape* of each hunk entry — it never checks that the *number* of hunks returned for a file matches the actual number of conflict markers present in that file on disk. [1](#0-0) 

The reassembly step then splices resolutions into conflict blocks purely by **positional order**, not by any identity/content binding to the specific marker block it was meant for: [2](#0-1) [3](#0-2) 

### Title
Missing hunk-count/identity validation in Copilot conflict-resolution splicing allows attacker-controlled branch content to silently corrupt unrelated conflict resolutions — (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`parseCopilotConflictResolution` validates that each resolution's `hunks` array is non-empty and that each `resolvedContent` is a string free of leftover conflict markers, but it never cross-checks the *count* of hunks against the number of `<<<<<<<`/`=======`/`>>>>>>>` blocks actually present in the corresponding file. `reassembleResolvedFile` then walks the raw file and consumes model-provided hunk resolutions strictly in array order, matching them to marker blocks positionally rather than by any bound identity (e.g., a hash of the original `oursContent`/`theirsContent` for that hunk).

### Finding Description
A user merging/rebasing/cherry-picking a branch that contains attacker-authored content (a very normal Desktop workflow — pulling a PR branch, merging a teammate's branch, or fetching from any remote) can end up with conflict hunks whose text is attacker-controlled. That text, including code comments, string literals, or other content inside the conflicting regions, is fed verbatim into the Copilot prompt as `oursContent`/`theirsContent`/context lines. If the model's output for a given file returns fewer (or reordered) hunks than the actual conflict-marker count in that file — something the validator does not catch, since it only requires `rawHunks.length > 0` — `reassembleResolvedFile`'s `hunkIndex` counter drifts out of sync with the real marker blocks it is walking. Every marker block after the drift point gets spliced with content intended for a *different* conflict, silently mixing attacker-supplied "theirs" content into a hunk the user believed was a different, reviewed resolution.

Because the check that exists (`rawHunks.length === 0` throws, `resolvedContent` must not contain conflict markers) never validates the relationship between the hunk array and the real number of conflict blocks per file, this is structurally the same class of bug as the Beranames issue: a validator covers some fields of the payload (shape, non-emptiness, no stray markers) but omits the field that actually matters for correctness/identity binding (hunk-to-marker-block correspondence), so an attacker who can influence the artifact (LLM output driven by attacker-authored branch content) can make the accepted payload diverge from what the user actually reviews and commits.

### Impact Explanation
The result is silent corruption of what the user commits or pushes: code from one conflict is spliced into another hunk's position without any error, warning, or marker mismatch surfaced to the user, who is shown only the reassembled full-file diff in the result dialog. This falls squarely under "silent corruption of what the user commits or pushes" from an attacker-controlled fetched/merged branch, since the attacker does not need local/physical access, admin rights, or prior malware — only the ability to author a branch/PR the victim merges.

### Likelihood Explanation
Exploitation is probabilistic rather than deterministic: it depends on the LLM being induced (via crafted comments/prompt-injection-style text embedded in the conflicting hunks) to emit a hunk count that doesn't match the true marker count for a file. Halborn-class reports of this shape are typically rated as valid despite this kind of non-determinism because the *validator itself* provides no defense — there is no code path that would catch or reject the mismatch even when it occurs.

### Recommendation
Bind each returned hunk resolution to the specific conflict block it targets — e.g., include a stable per-hunk identifier (index and/or hash of `oursContent`/`theirsContent`) in the prompt/schema, and have `parseCopilotConflictResolution` reject any resolution whose hunk count doesn't match the actual number of conflict-marker blocks in the corresponding `IFileConflictContext`, rather than only checking `rawHunks.length > 0`.

### Proof of Concept
1. Attacker opens a PR/branch with a file containing two independent conflict-marker blocks; block A's "theirs" side contains ordinary content, block B's "theirs" side contains a hidden backdoor plus prompt-injection text designed to make the model return only one hunk for the file (e.g. "resolve as a single combined hunk").
2. Victim merges the branch in Desktop and invokes "Resolve with Copilot".
3. The model returns `hunks: [ { resolvedContent: <benign-looking merge of block A> } ]` for the file (one hunk instead of two) — `parseCopilotConflictResolution` accepts it because `rawHunks.length === 1 > 0`.
4. `reassembleResolvedFile` (app/src/lib/copilot-conflict-resolution.ts:585-591) consumes this single hunk for marker block A, then falls out of sync: since there is no hunk left for block B (`hunkIndex >= hunkResolutions.length`), block B's marker content is simply dropped or the loop's positional assumption is violated depending on file layout — in files with 3+ hunks, hunk N's resolution gets spliced into marker block N+1 or N-1, silently mixing unrelated (potentially malicious) resolved content into a block the user believes is unrelated.
5. `_applyCopilotConflictResolutions` writes this corrupted reassembly straight to disk and stages it without further verification (app/src/lib/stores/app-store.ts:7258-7259), and the user commits it.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L423-450)
```typescript
    if (rawHunks.length === 0) {
      throw new CopilotValidationError(
        `Copilot returned an invalid conflict resolution payload: "hunks" at index ${i} must not be empty`
      )
    }

    const validatedHunks: Array<IHunkResolution> = []
    for (let j = 0; j < rawHunks.length; j++) {
      const hunkEntry: unknown = rawHunks[j]
      if (!isPlainObject(hunkEntry)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk at index ${j} of file "${path}" must be an object`
        )
      }
      const hunkObj = hunkEntry as Record<string, unknown>
      if (typeof hunkObj.resolvedContent !== 'string') {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: "resolvedContent" at hunk ${j} of file "${path}" must be a string`
        )
      }
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
      }
      validatedHunks.push({ resolvedContent: rc })
    }
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-546)
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
