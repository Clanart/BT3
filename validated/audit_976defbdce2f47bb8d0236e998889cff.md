## Analysis

The report's broken invariant is: a value flows through the system via two independent code paths that are assumed to agree, but one path can silently skip/misalign an entry while the other keeps counting sequentially by position, so a later item receives the wrong treatment with no error raised. The Desktop analog is in the Copilot AI conflict-resolution feature, where conflict markers are parsed twice by two independently-implemented "well-formedness" scanners that can disagree on adversarial/unusual marker sequences, causing model output to be spliced into the wrong region of a file that the user then commits and pushes — silently. [1](#0-0) [2](#0-1) 

### Title
Divergent conflict-marker parsers between `extractConflictHunks` and `reassembleResolvedFile` allow silent misapplication of AI conflict resolutions to the wrong file region - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
`extractConflictHunks` (used to build the prompt sent to Copilot) and `reassembleResolvedFile` (used to splice the model's per-hunk resolutions back into the on-disk file) each independently re-parse the raw conflicted file text for `<<<<<<<`/`=======`/`>>>>>>>` markers, using slightly different well-formedness rules. When the file being merged contains marker-like lines that are not part of a genuine two-way/three-way conflict block (e.g. a stray/unbalanced `<<<<<<<`-looking line, which can appear in a branch an attacker controls — as literal text, sample code, documentation about Git conflicts, or leftover markers from a previous unresolved conflict), the two scanners can disagree about where hunk boundaries begin and end. `extractConflictHunks` will silently drop a hunk it can't close (`continue` at line 241), while `reassembleResolvedFile`'s lookahead (lines 564-579) is more permissive and may treat a much larger span — potentially including a real, later conflict block — as a single "well-formed" region. Because hunk resolutions are matched purely by **positional order**, not by content or line numbers (explicitly documented at lines 535-536), this desynchronization causes the model's resolution intended for one conflict to be spliced over an unrelated region of the file, silently, with no validation that hunk counts match between extraction and reassembly.

### Finding Description
- Prompt-building path: `extractConflictHunks` walks the file line-by-line, and for every `<<<<<<<` marker it encounters, consumes lines until it finds `|||||||`/`=======`, then `>>>>>>>`. If no closing `>>>>>>>` is ever found, `hunkEnd` stays `-1` and the loop `continue`s, **silently discarding** that hunk entirely — it is never added to the returned hunk array and never sent to the model. [3](#0-2) 

- Reassembly path: `reassembleResolvedFile` independently re-walks the same raw file. At each line matching the ours-marker regex, it does an unbounded forward lookahead for *any* subsequent `=======` and `>>>>>>>` line — it does not require these to correspond to the "same" conflict block that `extractConflictHunks` identified, and it has no diff3/base-marker awareness at all. [4](#0-3) 

- The two functions can therefore disagree on (a) how many hunks exist, and (b) which line ranges each hunk spans, whenever the file contains marker-like lines that don't form a clean, matched triple in the exact way both parsers expect (e.g. an unterminated `<<<<<<<`, or literal text resembling conflict markers embedded in either side's content — plausible in real files such as documentation about Git, generated diff/patch fixtures, or a previous partially-resolved conflict).
- Because reassembly matches `hunkResolutions[hunkIndex]` strictly by encounter order (`hunkIndex` incremented once per well-formed block found during the *reassembly* scan) against resolutions the model produced for hunks enumerated by the *extraction* scan, a desync silently splices a resolution intended for one logical conflict into a different, potentially much larger, span of the file (including a genuine adjacent conflict block or surrounding correct code) — with no error, no validation, and no count check between the two passes.
- The corrupted content then flows straight to disk and `git add` when the user clicks "Continue Merge", via `_applyCopilotConflictResolutions`, which writes `resolution.resolvedContent` verbatim and stages it without re-diffing against the original hunks. [5](#0-4) 

Existing guards do not stop this path:
- `getHunkSkipReason` only gates on line length / total content size, not on marker well-formedness or on cross-validating extraction vs. reassembly hunk counts. [6](#0-5) 
- The "clobber protection" check in `_applyCopilotConflictResolutions` only guards against the user externally resolving the file in the meantime (`hasUnresolvedConflicts`); it does nothing to validate that the reassembled content correctly maps back to the original conflict regions. [7](#0-6) 
- `reassembleResolvedFile`'s own malformed-marker fallback ("copy through as regular content") only protects against markers with *no* subsequent separator/closing marker anywhere in the rest of the file — it does not protect against a stray marker whose lookahead happens to find a later, unrelated conflict's closing markers, which is exactly the desync scenario described above.

### Impact Explanation
This results in silent corruption of the file content the user commits and pushes after an AI-assisted merge/rebase/cherry-pick conflict resolution. The corruption is not surfaced anywhere in the UI (the result dialog shows the model's own per-file resolutions, not a re-validated diff against the actual file layout), so a user trusting "Continue Merge" can push code where AI-generated content for one conflict has overwritten unrelated, correct code (including code from the conflict that should have been preserved) without their knowledge. This falls squarely under "silent corruption of what the user commits or pushes," triggerable by content in a fetched/merged branch (attacker-influenced side of a merge/PR) — no local access, admin rights, or social engineering beyond a normal merge/PR workflow is required.

### Likelihood Explanation
The trigger condition — a file containing marker-like lines that don't cleanly close within the same block the extractor recognizes — is a scenario that can arise both accidentally (documentation/sample files containing literal Git conflict marker text, or files left with residual/partial conflict markers from a prior unresolved merge) and, more concerningly, can be deliberately engineered by a party who controls one side of a merge (e.g. a fork submitting a PR, or a malicious remote) to reliably desynchronize the two parsers. Because Copilot conflict resolution is an increasingly used one-click flow ("Resolve with Copilot"), and the desync is entirely silent (no exception, no validation, no diff shown), likelihood of an attacker successfully exploiting this without detection is non-trivial once file content is crafted to trigger the marker mismatch, though it requires the victim to actually invoke AI conflict resolution on the crafted file.

### Recommendation
- Make `extractConflictHunks` and `reassembleResolvedFile` share a single, canonical conflict-block parser (extract positions once, reuse both for prompt-building and for reassembly) rather than maintaining two independently-reimplemented regex scanners.
- After reassembly, assert that the number of well-formed blocks found in `reassembleResolvedFile` exactly equals `hunkResolutions.length` for that file (i.e., the same count `extractConflictHunks` reported); if they differ, fail the resolution for that file and surface it as a skipped/failed file rather than silently splicing a mismatched count.
- Additionally validate, per hunk, that the raw `oursContent`/`theirsContent` extracted for reassembly matches what was originally sent in the prompt (e.g. via a positional hash of hunk content) before splicing, to guard against any residual desync.

### Proof of Concept
1. A file `notes.md` in a repository contains, as ordinary content (not a real conflict), a lone unbalanced marker line such as:
   ```
   Example of a Git conflict marker: <<<<<<< HEAD
   (rest of paragraph, no matching ======= / >>>>>>> anywhere nearby)
   ```
2. Later in the same file, a genuine merge conflict occurs from a normal three-way merge:
   ```
   <<<<<<< HEAD
   safe_function_call();
   =======
   malicious_or_unrelated_call();
   >>>>>>> feature-branch
   ```
3. `extractConflictHunks` processes the stray `<<<<<<<` line first: it consumes all subsequent lines looking for a closing `>>>>>>>`, including consuming the entire real conflict block's `>>>>>>> feature-branch` line as its own "closing marker" match (per its own scan logic) or, depending on exact text, never finds a well-formed closing marker and drops the region — either way, it does not extract the same boundaries a naive line-count would expect, while `reassembleResolvedFile`, scanning independently, matches its own `<<<<<<<` occurrence to `=======`/`>>>>>>>` found later using a wider unbounded lookahead.
4. The single resolution the model produced for what it believed was "conflict 1 of 1" (the real code conflict) is placed by `reassembleResolvedFile` into the larger, incorrectly-bounded span identified by its independent scan — which can include the paragraph text and the real conflict — silently discarding/replacing content the model never saw or was told to preserve.
5. The user clicks "Continue Merge"; `_applyCopilotConflictResolutions` writes `resolution.resolvedContent` to disk and runs `git add`, staging the corrupted file for commit/push without any warning.

This scenario is not exhaustively verified against a live build (the exact desync boundaries depend on precise marker text and require dynamic testing of both `extractConflictHunks` and `reassembleResolvedFile` against crafted inputs), but the code-level asymmetry between the two independently-implemented parsers, and the absence of any cross-validation between extraction and reassembly hunk counts, is directly confirmed in the cited source.

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

**File:** app/src/lib/copilot-conflict-context.ts (L294-315)
```typescript
export function getHunkSkipReason(
  hunks: ReadonlyArray<IConflictHunk>
): string | null {
  let totalContent = 0

  for (const hunk of hunks) {
    const sides = [hunk.oursContent, hunk.theirsContent, hunk.baseContent ?? '']
    for (const side of sides) {
      totalContent += side.length
      for (const line of side.split('\n')) {
        if (line.length > MAX_CONFLICT_LINE_LENGTH) {
          return 'Conflict contains lines too long to resolve automatically'
        }
      }
    }
    if (totalContent > MAX_CONFLICT_CONTENT_SIZE) {
      return 'Conflict region too large to resolve automatically'
    }
  }

  return null
}
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
