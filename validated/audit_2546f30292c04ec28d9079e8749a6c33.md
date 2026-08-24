## Finding

### Title
AI-assisted conflict resolution can silently splice resolved content into the wrong conflict block, corrupting committed code without error - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
Desktop's Copilot-powered merge/rebase/cherry-pick conflict resolver uses two independently-written, hand-rolled parsers to walk the same on-disk conflicted file: one to extract hunks and build the LLM prompt (`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts`), and a separate one to splice the model's per-hunk answers back into the file (`reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts`). The splice step matches resolutions to conflict blocks **positionally, by order, not by content**, and the only safety check (`validateResolutionPaths`) verifies hunk *count*, not physical boundaries. A remote branch that contains lines coincidentally matching the marker regexes (`<{7}`, `={7}`, `>{7}` each followed by whitespace/EOL — e.g. documentation of git conflict markers, a leftover `.orig` file, or a linter fixture) can desynchronize the two parsers when a real conflict also exists in that file, causing the model's resolved content to be written into the wrong hunk or dropped — while the app reports success and no conflict markers remain.

### Finding Description
`extractConflictHunks` (`app/src/lib/copilot-conflict-context.ts:179-279`) scans a conflicted file for `<<<<<<<` / (optional `|||||||`) / `=======` / `>>>>>>>` sequences to build the hunks sent to the model, and silently discards (`continue`, not counted) any block for which no closing `>>>>>>>` is found <cite repo="bsaldua/desktop--023" path="app/src/lib/copilot-conflict-context.ts" start="239="242" /> [1](#0-0) .

`reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) re-walks the *same raw content* with its own copy of the marker regexes (which do not even recognize the diff3 `|||||||` base marker) to find block boundaries, and splices `hunkResolutions[hunkIndex]` into place strictly in encounter order: [2](#0-1) [3](#0-2) 

The function's own docstring acknowledges the load-bearing assumption: resolutions are matched "by order, not by line number" [4](#0-3) .

The only guard against a mismatch, `validateResolutionPaths`, checks that the number of hunks the model returned equals `expectedFiles` hunk counts — which come from `extractConflictHunks`, not from a re-scan of the raw file at splice time: [5](#0-4) 

Because both parsers implement subtly different first-match, greedy marker-detection algorithms (different regex sets, different malformed-block recovery: `extractConflictHunks` discards to EOF on an unmatched opening marker while `reassembleResolvedFile` recovers by advancing only one line and re-scanning), a file containing marker-like literal text in addition to a genuine conflict block can cause the two functions to disagree on the number or location of "hunks." When that happens, `reassembleResolvedFile` still produces output with zero remaining conflict markers, so:

- The written file passes the app's own "is this file still conflicted" check used at write time (`hasUnresolvedConflicts`) [6](#0-5) .
- The file is staged via `git add` and the merge/rebase/cherry-pick proceeds [7](#0-6) .
- No error, warning, or revert occurs anywhere in `_applyCopilotConflictResolutions`.

The end result mirrors the report's pattern precisely: the operation "succeeds" from the user's point of view (no error, dialog says "Continue Merge" completed), but the actual outcome (which code ended up in the file) is not what was validated or intended — a hunk's resolved content can be written into the wrong marker block, duplicated, or dropped entirely, silently corrupting the commit that gets created and later pushed.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." The corrupted content is written directly to the working tree and staged (`writeFile` + `git add`) with no verification that the reassembled file is a faithful merge of "ours" and "theirs" beyond marker removal [7](#0-6) . Because the resulting file has no conflict markers, neither Desktop's own heuristics nor a cursory user review of a "no more `<<<<<<<`" diff would flag it. Depending on which hunk is misplaced, this could silently drop a security-relevant change (e.g., an added validation check merged from one branch), reintroduce removed/vulnerable code, or duplicate logic — and that corrupted state gets committed and can be pushed to a shared remote.

### Likelihood Explanation
Triggering requires only that the victim (a) has the Copilot conflict-resolution feature enabled/selected during a merge/rebase/cherry-pick, and (b) merges against a branch/PR authored or influenced by the attacker that contains, anywhere in a file that will also carry a genuine conflict, literal text lines matching the marker patterns (e.g., a snippet demonstrating conflict-marker syntax in docs/tests, a committed `.orig`/`.rej` artifact, or minified/generated content). No local access, credentials, or unusual user action beyond the normal "resolve conflicts with Copilot → Continue Merge" flow is needed — this is exactly the "attacker controls a fetched/cloned repository" scenario. The bar is moderate rather than trivial because it depends on the exact interplay of the two parsers' malformed-block recovery paths, but the primitive (attacker-controlled file content driving divergent marker parsing) is concrete and reachable purely through git content the victim pulls in.

### Recommendation
- Replace the two independent, hand-written marker walkers with a single shared parser used both to build hunk context and to reassemble the file, so hunk boundaries can never diverge between the two phases.
- At splice time in `reassembleResolvedFile`, re-derive the hunk count from the same walk and hard-fail (throw `CopilotValidationError`) if it does not exactly match `hunkResolutions.length`, rather than silently truncating/skipping.
- After reassembly, verify no `<{7}`/`={7}`/`>{7}`-shaped lines remain unexpectedly, and diff the reassembled file's non-conflicted regions against the original to assert byte-for-byte equality outside the spliced ranges before staging.

### Proof of Concept
1. Attacker pushes a branch that modifies `docs/git-conflicts.md` (or any file) to include, as literal example text, a well-formed-looking marker fragment, e.g.:
   ```
   Example of a conflict marker block:
   <<<<<<< HEAD
   sample old text
   =======
   ```
   without a closing `>>>>>>>` in that spot (a common way to document conflict syntax).
2. Victim independently edits the same region of `docs/git-conflicts.md` on their own branch, so merging attacker's branch produces one *genuine* conflict block later in the file, e.g.:
   ```
   <<<<<<< HEAD
   victim's real change
   =======
   attacker's real conflicting change
   >>>>>>> feature
   ```
3. Victim runs the merge in Desktop and selects "Resolve with Copilot." `extractConflictHunks` walks the file: it hits the attacker's spurious `<<<<<<<`, fails to find a closing `>>>>>>>` before it reaches the *end of its inner scan* (because its malformed-hunk recovery consumes to EOF), and discards that block; depending on exact spacing it may instead absorb the genuine block into the same failed scan or shift the effective start of hunk collection.
4. `reassembleResolvedFile`, walking the identical raw content with its own recovery (advance one line and re-scan) instead of consuming to EOF, resolves the marker boundaries differently and ends up splicing the model's single resolved-content answer into the spurious/wrong location — into the attacker's documentation text or a shifted block — while the true conflict block is left copied through with its original (already-merged-away) content, or the model's answer for the real conflict is dropped.
5. `_applyCopilotConflictResolutions` finds the file has no remaining conflict markers, writes it via `writeFile`, stages it via `git add`, and the merge completes with no error surfaced to the user, who now commits/pushes a file whose content does not match what either the model or a manual reviewer approved.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-520)
```typescript
export function validateResolutionPaths(
  resolutions: ReadonlyArray<IRawFileResolution>,
  expectedFiles: ReadonlyArray<IFileConflictContext>
): void {
  const expectedPaths = new Set(expectedFiles.map(f => f.path))
  const expectedHunkCounts = new Map(
    expectedFiles.map(f => [f.path, f.hunks.length])
  )
  const returnedPaths = new Set(resolutions.map(r => r.path))

  for (const path of returnedPaths) {
    if (!expectedPaths.has(path)) {
      throw new CopilotValidationError(
        `Copilot returned resolution for unexpected file: ${path}`
      )
    }
  }

  if (returnedPaths.size !== resolutions.length) {
    throw new CopilotValidationError(
      'Copilot returned duplicate file paths in resolutions'
    )
  }

  const missingPaths: Array<string> = []
  for (const path of expectedPaths) {
    if (!returnedPaths.has(path)) {
      missingPaths.push(path)
    }
  }
  if (missingPaths.length > 0) {
    throw new CopilotValidationError(
      `Copilot did not return resolutions for: ${missingPaths.join(', ')}`
    )
  }

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-526)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L533-538)
```typescript
 * through verbatim. Each conflict marker block (`<<<<<<<` through
 * `>>>>>>>`, with a `=======` separator in between) is replaced with the
 * corresponding entry from `hunkResolutions` (matched by order, not by
 * line number). This guarantees that all non-conflicted code is preserved
 * exactly, and the model's output is only responsible for the small
 * resolved sections.
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

**File:** app/src/lib/stores/app-store.ts (L7258-7268)
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
    }
```
