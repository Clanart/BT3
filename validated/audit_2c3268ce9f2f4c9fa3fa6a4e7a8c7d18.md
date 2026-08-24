### Title
Attacker-controlled merge content can hijack conflict-marker boundary detection, causing GitHub Desktop to silently splice Copilot's resolution into the wrong location and commit corrupted content — ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`reassembleResolvedFile` in `app/src/lib/copilot-conflict-resolution.ts` reconstructs a conflict-resolved file by scanning the on-disk file line-by-line for git conflict markers and splicing in the model's per-hunk resolution text at the first matching marker triple it finds. The marker detection is done with three independent, permissive regexes and no verification that the number of detected marker blocks equals the number of hunks the model reasoned about. Because one side of a merge conflict ("theirs") is fully attacker-controlled content (an upstream branch/PR the victim merges), an attacker can embed look-alike marker lines inside their own hunk content to make the boundary scanner terminate a conflict block early, at a fake nested marker rather than the real one. [1](#0-0) 

### Finding Description
`reassembleResolvedFile` walks `rawContent` (the file as left on disk with conflict markers) line by line: [2](#0-1) 

When it sees a line matching `reassemblyOursMarker` (`/^<{7}(?:\s|$)/`), it scans forward for a line matching `reassemblySeparatorMarker` (`/^={7}$/`, sets a flag but does not stop) and then the *first* line matching `reassemblyTheirsMarker` (`/^>{7}(?:\s|$)/`), which is taken unconditionally as `closingIndex`:

```
for (let j = i + 1; j < lines.length; j++) {
  if (reassemblySeparatorMarker.test(lines[j])) {
    hasSeparator = true
  } else if (reassemblyTheirsMarker.test(lines[j])) {
    closingIndex = j
    break
  }
}
```

This logic assumes conflict markers can never legitimately appear as ordinary file content between the real `<<<<<<<` and `>>>>>>>` lines. That assumption fails for the "theirs" side of a conflict, which is fully attacker-controlled: an attacker can commit a file whose content includes lines that themselves look like conflict markers (e.g. `<<<<<<< x`, `=======`, `>>>>>>> x`) as ordinary text/config/string content. When git later produces a real conflict between the victim's local change and the attacker's branch, the resulting on-disk file looks like:

```
<<<<<<< HEAD          (real open)
victim's content
=======               (real separator)
<<<<<<< nested-fake    <- attacker content, looks like a marker
fake-a
=======                <- attacker content
fake-b
>>>>>>> nested-fake    <- attacker content, matches reassemblyTheirsMarker FIRST
...rest of attacker's real content...
>>>>>>> feature       (real close, never reached by the scan)
```

Because the scanner unconditionally breaks at the *first* line matching `>{7}(?:\s|$)`, it selects the attacker's fake `>>>>>>> nested-fake` line as `closingIndex` instead of the true `>>>>>>> feature` line. Everything from `closingIndex + 1` onward — including the real closing marker line, any remaining attacker content, and any subsequent unrelated file content — is then treated as ordinary passthrough lines (`resultLines.push(lines[i]); i++`) rather than as part of the resolved conflict, and is copied verbatim into the final file. Meanwhile the model's `resolvedContent` for that hunk (produced from a prompt built independently of this splicing logic) is inserted only up to the truncated boundary.

The Copilot model's own hunk output is checked for leftover conflict markers before reassembly: [3](#0-2) 

but this check runs on the model's `resolvedContent` string, not on the final file produced by `reassembleResolvedFile`, so it cannot catch marker-boundary corruption introduced by the splicing itself. There is also no check anywhere that the number of marker blocks detected in the raw file equals the number of hunks the model was asked to resolve.

The corrupted result then flows straight to disk and is auto-staged with no additional gate: [4](#0-3) 

The only pre-write guard in `_applyCopilotConflictResolutions` checks whether the user manually resolved the file externally in the meantime (`hasUnresolvedConflicts`) — it does not validate the structural correctness of the reassembled content itself: [5](#0-4) 

### Impact Explanation
This maps directly to the "silent corruption of what the user commits or pushes" impact category. An attacker who merely authors a branch or pull request that the victim later merges/rebases (a "cloned/fetched repository" content scenario, no elevated access needed) can force GitHub Desktop's "Resolve Conflicts with Copilot" feature to:
- drop legitimate resolved/original code from the file (truncated splice),
- leave literal git conflict-marker text (`>>>>>>> feature`, stray attacker fragments) embedded in the committed file, or
- misplace model-resolved hunk content relative to the real conflict regions.

Because the write path (`writeFile` + `git add`) runs automatically once the user clicks "Continue Merge" and there is no re-validation that the reassembled file is free of stray marker artifacts or structurally consistent with the original conflict count, the corrupted file can be committed and pushed without the victim noticing, unless they manually re-diff every hunk.

### Likelihood Explanation
Requires: (1) the victim's GitHub account/org has Copilot conflict resolution enabled and the victim clicks "Resolve with Copilot" on a merge/rebase/cherry-pick with conflicts, and (2) the attacker controls file content on one side of the conflict (their PR/branch). Both are realistic, unprivileged conditions — an external contributor's PR is exactly the kind of "theirs" content that ends up in a conflict. No special user interaction beyond the normal merge-conflict + "Resolve with Copilot" + "Continue Merge" flow is required, and no warning is shown that the reassembled output could contain artifacts from a boundary-detection mismatch.

### Recommendation
Harden `reassembleResolvedFile` (and the surrounding pipeline) so it does not rely on naive, independently-scanned regex matches to locate conflict boundaries:
- Track marker nesting/ordering strictly to require the separator and closing marker to be the *next* occurrence after the opening one at the same "depth", or better, derive hunk boundaries directly from the same marker set git itself produced (e.g., by re-parsing `git diff --check`/porcelain conflict info) rather than re-scanning raw text.
- After reassembly, verify the final content: assert `hunkIndex === totalDetectedBlocks` (fail loudly instead of silently truncating), and re-run the "no conflict markers remain" check against the *fully reassembled file*, not just the model's raw per-hunk string.
- Reject/flag hunks when the number of detected marker blocks in a file does not match the number of hunks the model was asked to resolve, surfacing the file to the user for manual resolution instead of auto-applying.

### Proof of Concept
1. Attacker opens a PR/branch containing a file, e.g. `config.txt`, whose content (which will end up as the "theirs" side of a future conflict) includes:
```
some real attacker content
<<<<<<< nested-fake
fake-a
=======
fake-b
>>>>>>> nested-fake
more real attacker content that should be kept
```
2. Victim has local changes to the same lines in `config.txt` and merges/rebases the attacker's branch, producing a conflict where git wraps the above content between real `<<<<<<< HEAD ... ======= ... >>>>>>> feature` markers.
3. Victim clicks "Resolve Conflicts with Copilot" then "Continue Merge".
4. In `reassembleResolvedFile`, the boundary scan starting at the real `<<<<<<< HEAD` line finds the attacker's `>>>>>>> nested-fake` line as `closingIndex` before it ever reaches the true `>>>>>>> feature` line.
5. The model's resolved hunk content is spliced in at the truncated boundary, and everything from `nested-fake`'s closing line onward (including the real `>>>>>>> feature` marker and "more real attacker content that should be kept") is copied through verbatim as ordinary file content.
6. `git add` stages this corrupted file; the victim commits/pushes a file containing stray conflict-marker text and/or misplaced hunk content without any error or warning.

Note: I was unable to inspect `app/src/ui/multi-commit-operation/dialog/copilot-conflicts-changes.tsx` (the result-dialog diff preview) directly due to a tool error in the final iteration, so I cannot fully confirm whether the diff view would visually surface the stray marker text to the user before they click "Continue Merge." This should be verified in a follow-up session, as it affects whether the corruption is truly "silent" or merely easy to overlook.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L438-449)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L523-599)
```typescript
// Conflict markers used by reassembleResolvedFile to locate marker blocks.
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/

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
 */
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

**File:** app/src/lib/stores/app-store.ts (L7233-7268)
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
    }

    if (pathsToStage.length > 0) {
      await git(
        ['add', '--', ...pathsToStage],
        repository.path,
        'copilotConflictResolution'
      )
    }
```
