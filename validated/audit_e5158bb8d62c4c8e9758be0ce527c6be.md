### Title
Ambiguous conflict-marker regex lets attacker-controlled file content desynchronize Copilot's merge-conflict hunk boundaries, causing silent code loss on commit - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The bug class in the report is: a value used for the actual operation (`amountOut`) diverges from the value the contract *believes* it deposited (`deposit1`), silently stranding funds. The Desktop analog is in the AI merge-conflict resolution pipeline: the function that extracts conflict hunks to send to the model (`extractConflictHunks`) and the function that later splices the model's resolutions back into the file (`reassembleResolvedFile`) both rely on a bare regex, `/^={7}$/`, to decide where a conflict hunk begins and ends — with no way to distinguish a real git-inserted `=======` separator from an identical line that is simply part of the file's own content (a very common convention in Markdown/RST/plain-text dividers). Because *ordinary, attacker-controlled repository content* can match this marker, the two functions can each independently misidentify hunk boundaries, causing the model to see corrupted `ours`/`theirs` text and `reassembleResolvedFile` to splice its (wrong) output over legitimate code — silently corrupting what gets committed, with no error surfaced.

### Finding Description
Both files define the same “separator” marker: [1](#0-0) [2](#0-1) 

`extractConflictHunks` (used to build the prompt sent to Copilot) walks the file and, once it sees `<<<<<<<`, stops collecting "ours" content at the **first** line matching `/^={7}$/` or `/^\|{7}/`: [3](#0-2) 

`reassembleResolvedFile` (used to splice the model's answer back into the on-disk file) independently re-scans the same file and treats the block as a valid hunk as soon as it sees **any** `/^={7}$/` line followed eventually by a `>>>>>>>` line: [4](#0-3) 

Neither function verifies that the `=======` it matched is the one git actually inserted for that conflict (git always emits exactly one real separator per hunk, but any file whose legitimate "ours" or "theirs" text happens to contain a line of exactly seven `=` characters — a common section-divider convention — will falsely satisfy this regex). If such a line appears inside the true "ours" span (i.e., between the real `<<<<<<<` and the real `=======`), `extractConflictHunks` truncates `oursContent` early and merges the remainder of the real ours text, the true separator line, and the real theirs text all into `theirsContent`. The model is prompted with this mislabeled, garbled context (Copilot has no tools and cannot verify it — `selectReferencedContext`'s own comment notes "the model can only ever cite data we placed in the prompt"), and produces a `resolvedContent` based on incorrect understanding of what each side actually changed.

Crucially, `reassembleResolvedFile` performs its own boundary scan and, since it only requires "any `=======`-like line exists before the next `>>>>>>>`," it agrees on the same overall `<<<<<<<`…`>>>>>>>` span as one hunk — so `validateResolutionPaths`'s hunk-count check passes: [5](#0-4) 

The entire span (including the legitimate tail of "ours" that the model never actually saw as "ours") is then unconditionally replaced with the model's `resolvedContent`: [6](#0-5) 

The reassembled content is subsequently written to disk and offered up for staging/commit through the standard commit path (`createCommit` → `stageFiles`), with no re-validation that the written content matches user intent: [7](#0-6) [8](#0-7) 

There is no guard anywhere in this pipeline that checks whether `resolved.length` vs. the true dropped content differs, nor any confirmation step that surfaces the discarded lines to the user before the resolution is applied — the corruption is invisible.

### Impact Explanation
This directly matches the "silent corruption of what the user commits" impact category. An attacker who controls a branch/PR the victim later merges (a very ordinary open-source workflow) can craft a file so that a real merge conflict against it, combined with an incidental divider line already present in the shared file, causes Copilot's automated conflict resolution to systematically drop or scramble a chunk of the user's own "ours" code without any error, warning, or diff review flagging the loss (the dialog only shows the model's self-reported `reasoning`, which reflects its corrupted understanding, not the true discrepancy). This can silently remove security checks, feature code, or configuration the user believed they kept, and the corrupted result is what gets staged and committed.

### Likelihood Explanation
Exploitation requires only that a repository contain, in ordinary tracked files (README, CHANGELOG, RST/Markdown docs, ASCII-art separators, etc.), a line of exactly seven `=` characters positioned inside what becomes the "ours" region of a real merge conflict against an attacker-authored branch, and that the user opts to use the Copilot conflict-resolution feature on that conflict. This requires no local access, no credentials, and no unnatural user steps — the user just performs a normal merge/rebase and clicks the AI resolve feature, which is precisely the intended UX. The main uncertainty is how frequently `=======`-style dividers exist in real conflicted regions; this is a content-shape dependency rather than a hardening gap, but it is fully reachable and requires no unusual interaction beyond normal git merge conflict handling.

### Recommendation
- Do not rely on bare `/^={7}$/` (and `/^<{7}/`, `/^>{7}/`) matching arbitrary file content as authoritative conflict markers. Track the *exact* marker lines returned by git (e.g., via `git diff --check`/`ls-files -u`/`merge-file` metadata, or by requiring the marker line to carry the branch-name suffix git appends to `<<<<<<<`/`>>>>>>>`) so hunk boundaries used for both prompt construction and reassembly are unambiguous.
- Make `extractConflictHunks` and `reassembleResolvedFile` share a single, canonical parser/boundary list instead of two independently-reimplemented scans, eliminating any chance of divergent interpretation of the same file.
- After reassembly, diff the final resolved content's non-conflicted regions against the original file to assert byte-for-byte preservation outside the actual hunk spans, and fail loudly (falling back to manual resolution) if unexpected drift is detected.

### Proof of Concept
1. On `main`, create `notes.md`:
```
Intro text
=======
Real body content that must be preserved
Second real line to preserve
```
(the `=======` here is an intentional divider used in the doc, not a git marker)
2. On `feature`, modify the "Second real line to preserve" line differently.
3. Merge `feature` into `main`; git inserts real markers around the differing region, e.g.:
```
Intro text
<<<<<<< HEAD
=======
Real body content that must be preserved
Second real line to preserve
=======
Real body content that must be preserved
Second real line to preserve (feature edit)
>>>>>>> feature
```
4. Run the Copilot conflict resolution flow. `extractConflictHunks` stops "ours" collection at the *first* `=======` it meets (the doc's own divider, immediately after `<<<<<<< HEAD`), so it sends the model an empty/near-empty "ours" and a "theirs" blob that is actually the concatenation of the true ours text, the real separator, and the true theirs text.
5. The model, seeing "ours" as effectively empty, resolves by picking what it believes is "theirs" — but that blob contains a stray literal `=======` line and duplicated content, which the model will normalize/collapse, silently dropping the distinction between the two real sides.
6. `reassembleResolvedFile`, independently confirming only that *some* `=======`-like line exists before the next `>>>>>>>`, splices this single wrong `resolvedContent` over the entire true `<<<<<<< HEAD` … `>>>>>>> feature` span.
7. The resulting `notes.md` staged and committed via `createCommit` no longer faithfully reflects either branch's real content — a silent corruption of what the user committed, with `validateResolutionPaths`'s hunk-count check (1 expected, 1 returned) passing throughout.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-context.ts (L200-226)
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L524-526)
```typescript
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
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

**File:** app/src/lib/git/commit.ts (L15-31)
```typescript
export async function createCommit(
  repository: Repository,
  message: string,
  files: ReadonlyArray<WorkingDirectoryFileChange>,
  options?: {
    amend?: boolean
    noVerify?: boolean
    signOff?: boolean
    allowEmpty?: boolean
  } & HookCallbackOptions
): Promise<string> {
  // Clear the staging area, our diffs reflect the difference between the
  // working directory and the last commit (if any) so our commits should
  // do the same thing.
  await unstageAll(repository)

  await stageFiles(repository, files)
```

**File:** app/src/lib/git/update-index.ts (L109-129)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }
```
