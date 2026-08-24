## Title
Conflict-resolution reassembly re-parses conflict markers independently from hunk extraction, letting attacker-controlled merge content splice AI resolutions into the wrong location - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
Knox's `_finalizeAuction` bug is: two code paths are supposed to derive the same terminal value from the same state, but on a non-"happy path" (auction doesn't sell out) one path silently substitutes the last-seen value instead of the correct one, mispricing the trade. Desktop's Copilot merge-conflict-resolution feature has the same broken-invariant shape: the number/position of conflict hunks is computed twice, by two independently hand-rolled, non-nesting-aware marker parsers over the same raw file — once in `extractConflictHunks` [1](#0-0)  (used to build the prompt sent to the model), and again in `reassembleResolvedFile` [2](#0-1)  (used to splice the model's per-hunk resolutions back into the file that gets written to disk and committed). When attacker-controlled merge content causes these two parsers to disagree on where hunk boundaries actually are (while still agreeing on total *count*), `reassembleResolvedFile` silently writes the AI's resolution for hunk *N* into the wrong marker block.

### Finding Description
`extractConflictHunks` walks a conflicted file line-by-line with a simple non-nesting state machine: for each `<<<<<<<` it collects "ours" lines until `=======` (or `|||||||`/base for diff3), then "theirs" lines until the next `>>>>>>>`. If no closing `>>>>>>>` is ever found, the hunk is silently discarded via `continue`, and because `i` has already been advanced to `lines.length`, **all further real conflicts later in the file are never parsed** [3](#0-2) . This hunk count becomes `expectedHunkCounts` and is what the model is told about via `"Conflict i of N"` in the prompt [4](#0-3) .

`reassembleResolvedFile`, used later to splice the model's `hunkResolutions` back into the on-disk content, re-implements the *same* marker scan independently, with different edge-case behavior: it looks ahead for the first line matching `=======` and the first *subsequent* line matching `>>>>>>>`, and treats that as one complete conflict block regardless of what's between them (no nesting/diff3 awareness, no coordination with `extractConflictHunks`'s state) [5](#0-4) . The docstring explicitly states resolutions are "matched by order, not by line number" [6](#0-5) .

`validateResolutionPaths` is the only guard tying the two together, and it only checks that the *count* of hunks the model returned equals `expectedFiles`'s hunk count — it never checks that the block boundaries themselves match: `resolution.hunks.length !== expectedCount` [7](#0-6) .

Because the file content being scanned (the "ours"/"theirs" hunk bodies) originates directly from the branches being merged — one of which is entirely attacker-controlled (a malicious PR branch, a spoofed remote, or an upstream fork the victim merges/rebases against) — an attacker can craft content that contains marker-look-alike character runs (e.g. a README snippet about git conflicts, a vendored `.patch`/`.diff` file, or literal `<<<<<<<`/`=======`/`>>>>>>>`-style text embedded in a string or comment) inside a conflict hunk's body. This causes the two independent scanners to disagree about *where* a hunk begins/ends while still agreeing on the total hunk *count* for that file, since `extractConflictHunks`'s early-truncation behavior and `reassembleResolvedFile`'s greedy first-match behavior are not equivalent state machines over the same input in general. `validateResolutionPaths` sees matching counts and passes, then `reassembleResolvedFile` splices `hunkResolutions[k]` — the model's answer for what it believed was hunk *k* based on `extractConflictHunks`'s markup — into a differently-bounded block identified by its own independent scan.

### Impact Explanation
The result is silent corruption of what the user commits/pushes: the AI-generated merge resolution for one conflict gets written into the wrong location of the file (or the reassembled file drops/duplicates code, or leaves stray marker-like content behind that isn't actually flagged since the validation in `parseCopilotConflictResolution` only checks the *model's returned* `resolvedContent` for marker text, not the final reassembled file) [8](#0-7) . The user, trusting the "AI resolved this conflict" flow, stages and commits this silently-wrong content without markers being visibly present, potentially reintroducing a bug the merge was supposed to fix, dropping security-relevant code from one side of the merge, or committing content the attacker steered into place. This satisfies the "silent corruption of what the user commits or pushes" impact class from an entirely attacker-controlled git ref, with no local access or social engineering step beyond the normal act of merging/fetching a branch.

### Likelihood Explanation
This requires: (1) the victim to use the Copilot-assisted conflict resolution feature, (2) a merge/rebase/cherry-pick against attacker-influenced content (a PR branch, a compromised/malicious remote, or a third-party fork), and (3) that content to contain a crafted marker-like sequence inside a conflicted hunk. Given how common it is for repositories to contain literal example conflict-marker text (documentation, tutorials, vendored patch files, test fixtures) this is a plausible, low-effort craft for an attacker who can get their branch merged or fetched into a conflict scenario. It does not require any elevated privilege, admin rights, or pre-existing host compromise — only an ordinary merge with attacker-supplied content, which is squarely in-scope for the requested threat model (attacker controls a cloned/fetched repository or git remote/proxy response).

### Recommendation
Make `extractConflictHunks` and `reassembleResolvedFile` share a single conflict-marker parser/AST so hunk boundaries used to build the prompt are guaranteed identical to the boundaries used for splicing, rather than re-implementing the marker state machine twice with different edge-case handling. At minimum, `reassembleResolvedFile` should reuse `extractConflictHunks`'s exact block offsets (start/end line indices) instead of re-scanning, and `validateResolutionPaths`/reassembly should fail closed (throw `CopilotValidationError`) if the independently-detected block count or boundaries during reassembly don't exactly match what was extracted for the prompt, rather than silently proceeding on a count-only match.

### Proof of Concept
1. Attacker pushes a branch that, in a file the victim will also modify, includes body content such as a documentation/example blob containing literal marker-like text, e.g. embed inside the attacker's changed lines:
   ```
   Example of a conflict:
   <<<<<<< HEAD
   old
   =======
   new
   >>>>>>> feature
   ```
   as ordinary file content (not an actual unresolved conflict) adjacent to/inside a real conflicting hunk.
2. Victim merges/rebases the attacker's branch, producing a real git conflict elsewhere in the same file, so the file now contains one genuine `<<<<<<<...=======...>>>>>>>` block plus the attacker's embedded look-alike text.
3. `extractConflictHunks` walks the file: depending on ordering, it may terminate hunk collection early or misalign which lines belong to "ours"/"theirs" for the second real conflict because its non-nesting scanner treats the embedded look-alike lines as real markers. `formatConflictContextForPrompt` sends the model an incorrect/incomplete view of hunk boundaries.
4. The model returns exactly the hunk count expected by `expectedHunkCounts` (since it's just responding to whatever `extractConflictHunks` sent it), passing `validateResolutionPaths`'s count check.
5. `reassembleResolvedFile` re-scans the same `rawContent` independently and finds a different set of block boundaries (since its lookahead differs from `extractConflictHunks`'s sequential state machine), so `hunkResolutions[hunkIndex]` — the model's answer keyed to the *extraction*'s hunk order — gets spliced into a block boundary computed by the *reassembly* scan, which is not guaranteed to be the same region.
6. The victim reviews a diff that looks plausible (no visible conflict markers) and commits/pushes the resulting silently-misassembled file.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L179-242)
```typescript
export function extractConflictHunks(
  fileContent: string,
  contextLines: number = 3
): ReadonlyArray<IConflictHunk> {
  const lines = fileContent.split(/\r?\n/)
  const hunks: Array<IConflictHunk> = []

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L443-449)
```typescript
      const rc = hunkObj.resolvedContent
      if (/^<{7}\s/m.test(rc) && /^={7}$/m.test(rc)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk ${j} of file "${path}" still contains conflict markers`
        )
      }
      validatedHunks.push({ resolvedContent: rc })
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L533-536)
```typescript
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L549-599)
```typescript
export function reassembleResolvedFile(
  rawContent: string,
  hunkResolutions: ReadonlyArray<IHunkResolution>
): string {
  const eol = rawContent.includes('\r\n') ? '\r\n' : '\n'
  const lines = rawContent.split(/\r?\n/)
  const resultLines: Array<string> = []
  let hunkIndex = 0
  let i = 0

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

  return resultLines.join(eol)
}
```
