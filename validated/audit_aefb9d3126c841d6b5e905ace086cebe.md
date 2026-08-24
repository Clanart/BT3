### Title
Silent conflict-marker misdetection in Copilot conflict resolution reassembly corrupts committed files - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
GitHub Desktop's Copilot-assisted conflict resolution feature parses a conflicted file's marker structure twice, with two different, non-equivalent parsers: once in `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-279`) to build the prompt sent to the model, and again in `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) to splice the model's per-hunk resolutions back into the on-disk file. These two parsers disagree on what counts as a valid conflict block when a file (which an attacker fully controls via a branch/PR/fetched ref) contains conflict-marker-*looking* lines that are not actually the boundary of the same block. When they disagree, `reassembleResolvedFile` can conclude a real conflict block is malformed and copy the raw, still-conflicted text through verbatim — while the surrounding validation (`validateResolutionPaths`) never re-checks the reassembly-time parse, so nothing catches the mismatch. The result is a file that still contains literal `<<<<<<<`/`=======`/`>>>>>>>` markers (or otherwise-unresolved/garbled content) being written to disk, staged with `git add`, and silently presented to the user as "resolved by Copilot."

### Finding Description
`extractConflictHunks` and `reassembleResolvedFile` use marker regexes that look identical (`/^<{7}(?:\s|$)/`, `/^={7}$/`, `/^>{7}(?:\s|$)/`) but apply them with different state machines:

- `extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-242`): after seeing `<<<<<<<`, it collects "ours" lines by scanning only for `baseMarker` or `separatorMarker` — it does **not** treat a stray `>>>>>>>`-looking line inside that span as anything special; it becomes ordinary "ours" content. [1](#0-0) 

- `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:559-596`): after seeing `<<<<<<<`, it scans forward and **immediately breaks on the first line matching the "theirs" marker**, regardless of whether a `=======` separator was seen first. [2](#0-1) 

If a `=======` separator hasn't been seen yet when that first theirs-marker-looking line is hit, `hasSeparator` is `false`, so the whole block is classified "malformed" and copied through **verbatim, including the original conflict markers and both sides' unresolved content**: [3](#0-2) 

Concretely, a file containing this (a real conflict, adjacent to text that merely resembles a marker — plausible in READMEs/tutorials about Git, changelogs quoting conflict markers, or fixture/test files):
```
<<<<<<< HEAD
some text
>>>>>>> unrelated-looking-line
more ours content
=======
their content
>>>>>>> feature
```
is parsed by `extractConflictHunks` as **one valid hunk** (`hunks.length === 1`), which is what gets sent to Copilot and is the count `validateResolutionPaths` (`app/src/lib/copilot-conflict-resolution.ts:473-521`) checks the model's response against. But `reassembleResolvedFile`, run afterward on the very same `rawContent`, treats this block as malformed on its first pass (`hasSeparator === false` when it hits the stray theirs-like line) and pushes the **original, still-conflicted lines straight into `resultLines`** without ever consuming `hunkResolutions[0]` — the model's real fix is dropped entirely.

`validateResolutionPaths` only compares the *model's declared* hunk count to the count from `extractConflictHunks`; it never re-derives or cross-checks against `reassembleResolvedFile`'s own scan of `rawContent`, so this divergence is completely unguarded. The reassembled (still broken) content is then written straight to disk and staged: [4](#0-3) 

The only safety check before writing is a path-traversal guard (`resolveWithin`) and a check for whether the user already resolved the file externally — neither of which validates the *content* being written is actually free of conflict markers. [5](#0-4) 

### Impact Explanation
This is a silent-corruption-of-what-the-user-commits bug: the user believes Copilot fully resolved the merge/rebase/cherry-pick conflict (the dialog reports success), but the file that gets staged and eventually committed/pushed still contains literal, syntactically-invalid Git conflict markers and unmerged content from both branches. An attacker who controls one side of the merge (a malicious branch, PR, or fetched ref) can deliberately place marker-like text near a real conflict to trigger this misparse, causing broken/garbled code to be silently committed and potentially pushed by the victim without them noticing — this can break builds, reintroduce vulnerable/removed code, or hide malicious code changes inside what looks like a clean, AI-resolved merge.

### Likelihood Explanation
Triggering the two-parser divergence requires only a specially crafted file in a branch/PR/ref the victim merges or rebases with GitHub Desktop's Copilot conflict resolution feature enabled — a fully attacker-controlled, unprivileged input path (the report's allowed primitive: "attacker controls a cloned/fetched repository"). No special user action beyond the normal merge/rebase + "resolve with Copilot" workflow is required, and the mismatch is not something a casual reviewer would notice unless they specifically re-diff the resolved file for leftover markers.

### Recommendation
Make `reassembleResolvedFile` use exactly the same block-boundary state machine as `extractConflictHunks` (ideally, share one implementation) so both agree on where a conflict block begins/ends, including proper handling of `|||||||` (diff3) and never treating a marker line as significant unless it is genuinely a boundary reached in the correct order (`<<<<<<<` → optional `|||||||` → `=======` → `>>>>>>>`). Additionally, after reassembly, re-run `getFilesWithConflictMarkers`/a marker scan on the produced `resolvedContent` and refuse to write/stage the file (surfacing it to the user as unresolved) if any conflict markers remain, rather than silently accepting whatever `reassembleResolvedFile` produced.

### Proof of Concept
1. Create a merge conflict where the conflicted file also contains a line that matches the "theirs" marker pattern (`^>{7}(?:\s|$)`) *before* the real `=======` separator of a legitimate conflict block, e.g.:
```
<<<<<<< HEAD
example: >>>>>>> not a real marker
kept ours line
=======
kept theirs line
>>>>>>> feature
```
2. Trigger GitHub Desktop's "Resolve with Copilot" flow. `extractConflictHunks` reports this as 1 hunk and sends it to the model; the model returns 1 `resolvedContent` hunk, which passes `validateResolutionPaths`.
3. `reassembleResolvedFile(rawContent, [hunkResolution])` is called: at `i=0` it finds `<<<<<<<`, scans forward, hits the embedded `>>>>>>>`-looking line at `j=2` before any `=======` was seen, sets `closingIndex=2` with `hasSeparator=false`, and takes the "malformed marker" branch — pushing the original `<<<<<<<` line through unchanged and continuing line-by-line. The rest of the actual conflict (`=======`, both sides' content, `>>>>>>>`) is copied through verbatim as well, since none of the subsequent lines individually match the ours-marker check that drives special handling.
4. The resulting `resolvedContent` — still containing raw conflict markers — is written to disk via `writeFile` and staged via `git add` in `app-store.ts`, while the UI reports the file as resolved. [6](#0-5) [7](#0-6)

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
