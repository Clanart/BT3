### Title
Embedded conflict-marker-like lines in "ours"/"theirs" content desynchronize Copilot's conflict-hunk parser, causing stray marker text to be silently committed - (File: `app/src/lib/copilot-conflict-context.ts`, `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
The GMX bug is a classic "computed value never stored / boundary miscalculated" defect that silently corrupts downstream accounting. The Desktop analog is in the new AI conflict-resolution feature: the line-based conflict-marker scanner used both to build the model prompt (`extractConflictHunks`) and to splice the model's answer back into the file (`reassembleResolvedFile`) determines hunk boundaries purely by regex-matching individual lines, with no validation that a marker line encountered while *inside* a hunk's ours/theirs content is genuine rather than attacker-supplied text that merely looks like a marker. A crafted line inside the conflicting content (fully attacker-controlled via a cloned/fetched branch or commit) makes the scanner close the hunk early, leaving the true closing marker and any trailing code as ordinary, "already resolved" file content that is never sent to the model and never removed.

### Finding Description
`extractConflictHunks` locates a hunk by matching `oursMarker` (`^<{7}...`) and then greedily consumes lines into `theirsLines` until it sees *any* line matching `theirsMarker` (`^>{7}(?:\s|$)`): [1](#0-0) 

There is no check that the `>>>>>>>` (or `=======`/`|||||||`) line actually belongs to the current hunk versus being literal content contributed by one side of the merge (e.g. a source file, doc, or test fixture that contains example conflict-marker text, or an attacker deliberately padding a commit with such a line). Once a "closing" marker is matched prematurely, the outer scan loop resumes treating the *real* closing marker line, and everything after it, as ordinary unconflicted file content: [2](#0-1) 

The exact same weakness exists independently in the splice-back path, `reassembleResolvedFile`, which re-scans the raw on-disk content with its own copy of the same marker regexes and the same "first marker line wins" logic: [3](#0-2) 

Because both functions apply the identical flawed boundary rule to the same file, the hunk *count* stays consistent (so `validateResolutionPaths`'s count check does not catch it): [4](#0-3) 

but each "hunk" is a truncated fragment of the real conflict. The model is asked to resolve only the truncated fragment and returns `resolvedContent` for it; the genuine trailing portion of the conflict — including the real `>>>>>>> branch` marker line — is copied through verbatim by the `else` branch of both scanners as if it were already-resolved code: [5](#0-4) 

The reassembled content is then written straight to disk and staged with `git add`, with no re-check for leftover conflict markers before staging/committing: [6](#0-5) 

The validator that does check for markers only inspects the model's own `resolvedContent` for each hunk, not the reassembled, spliced final file, so it cannot catch marker text that leaked in from the untouched tail of the original content: [7](#0-6) 

### Impact Explanation
This corrupts what the user commits without any error or warning: the file Copilot writes and stages can contain a literal, syntactically-broken `>>>>>>> <branch>` line (and any code between the fake and real closing markers is silently excluded from AI resolution while still being kept verbatim, unreviewed). Once staged, `_finishCopilotConflictResolution` moves straight to commit creation for the merge/rebase/cherry-pick, so this broken content can be committed and pushed to a shared remote — a silent corruption of the user's commit content that meets the "silent corruption of what the user commits or pushes" impact bar. The trigger is fully attacker-controlled: any branch/commit fetched from a remote that contains a line matching `^>{7}(?:\s|$)`, `^={7}$`, or `^\|{7}(?:\s|$)` inside code that later conflicts with the local branch (e.g., a source comment, a git-tutorial doc, a CI/config template, or a deliberately crafted commit) is sufficient — no local access, no elevated privileges, no user error required beyond running the normal "resolve conflicts with Copilot" flow.

### Likelihood Explanation
Lines that incidentally match `<<<<<<<`, `=======`, or `>>>>>>>` at the start of a line are not exotic — documentation about git, patch/diff fixtures, or scripts that print merge markers can contain them naturally, and an attacker who controls a branch merged/rebased against can trivially plant one deliberately. Any repository maintainer merging in changes from a fork, dependency vendoring branch, or contributor PR is exposed the moment they opt into Copilot conflict resolution, making this a realistic supply-chain-style content-injection primitive with a straightforward exploitation path.

### Recommendation
Track marker-block nesting depth (or require markers to be validated globally before hunk extraction, e.g. by first verifying the file has a well-formed, non-nested sequence of `<<<<<<<`/`(|||||||)`/`=======`/`>>>>>>>` blocks) rather than closing a hunk on the first line that merely matches the closing-marker regex. Additionally, run the existing "still contains conflict markers" check (currently only applied to each hunk's `resolvedContent`) against the *final reassembled file* before it is written to disk and staged, so any residual marker line — whatever its origin — blocks the write instead of being silently committed.

### Proof of Concept
1. Attacker pushes/serves a branch `evil` where a file `notes.md` (or any tracked file) contains, as ordinary content:
```
Example of how conflicts look:
<<<<<<< sample
placeholder
=======
this text ends with a fake close: >>>>>>> sample
real trailing code that must not be dropped
=======
>>>>>>> real-marker-that-was-supposed-to-close-the-conflict
```
2. Victim merges/rebases their branch with `evil`, producing a real conflict in this file whose "theirs" side is exactly the block above.
3. `extractConflictHunks` starts at the true `<<<<<<<`, then while collecting `theirsLines` hits the embedded `>>>>>>> sample` line first and treats it as `hunkEnd`, per `app/src/lib/copilot-conflict-context.ts:228-242`.
4. The victim runs "Resolve with Copilot." The model only sees/resolves the truncated fragment; `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:559-591`) performs the same truncated match on disk and reproduces the same boundary, leaving `real trailing code that must not be dropped` and the literal `>>>>>>> real-marker-that-was-supposed-to-close-the-conflict` line untouched in the "resolved" output.
5. `writeFile`/`git add` stage this file as conflict-free (`app/src/lib/stores/app-store.ts:7258-7268`), and it can be committed/pushed with a stray, unresolved-looking marker line and unreviewed injected code baked into history.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L186-191)
```typescript
  let i = 0
  while (i < lines.length) {
    if (!oursMarker.test(lines[i])) {
      i++
      continue
    }
```

**File:** app/src/lib/copilot-conflict-context.ts (L228-242)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L592-595)
```typescript
    } else {
      resultLines.push(lines[i])
      i++
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
