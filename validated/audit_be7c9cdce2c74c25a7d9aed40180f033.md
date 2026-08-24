### Title
Copilot-resolved file content is written to disk and staged without validating the reassembled output, allowing an untrusted LLM response to silently corrupt what the user commits - (File: `app/src/lib/stores/app-store.ts`)

### Summary
The external report's broken invariant is: a value produced by an untrusted/variable source (a swap's output amount) is consumed and acted upon irreversibly (transferred) without checking it meets a minimum acceptable bound. The closest analog in GitHub Desktop is the Copilot conflict-resolution feature: content produced by an LLM (an untrusted, non-deterministic source that is fed attacker-influenced context — commit messages, PR descriptions, and file contents from a cloned/fetched repository) is spliced into the user's files and then written to disk and `git add`-ed without any final check that the result is sane, before the user is asked to commit/continue the merge.

### Finding Description
`reassembleResolvedFile` in [1](#0-0)  only validates that the *original on-disk* conflict markers are well-formed; it does not check that the LLM-supplied `resolvedContent` for each hunk is itself free of conflict markers, non-empty when expected, or otherwise consistent with the surrounding code. `parseCopilotConflictResolution`/`validateResolutionPaths` validate JSON shape and that path/hunk counts match, but never inspect the actual resolved text quality: [2](#0-1) .

The reassembled `resolution.resolvedContent` is then written directly to the working tree and staged in `_applyCopilotConflictResolutions`: [3](#0-2) 

The only pre-write guard is a path-traversal check (`resolveWithin`) and a check that the file wasn't already resolved externally by the user; there is no content-quality gate before the file is overwritten and `git add`-ed: [4](#0-3) .

Because the conflict-resolution prompt embeds commit messages, PR titles/descriptions, and the actual conflicting hunks from a cloned/fetched repository (attacker-controlled inputs, since Desktop's threat model treats fetched remote content as untrusted), a maliciously crafted commit message or file content can attempt to steer the model into producing plausible-looking but subtly wrong or malicious merged code (e.g. removing a security check, reintroducing vulnerable code, or leaving behind an unbalanced/hidden marker like a stray `<<<<<<<` inside a string literal that `reassembleResolvedFile`'s look-ahead treats as "regular content" per its own documented fallback). None of this is caught before the content is written and staged — it is only surfaced to the user as diff text in the result dialog, which they may not carefully re-review before clicking "Continue Merge".

### Impact Explanation
If the injected/incorrect resolution is accepted (or missed during review), the corrupted content is staged and later committed and potentially pushed, silently reintroducing vulnerable or incorrect code into the repository's history — this matches the "silent corruption of what the user commits or pushes" impact category. Unlike a swap's slippage loss, there is no automatic detection or rollback; the only safeguard is the human reviewing the diff in `CopilotConflictsDialog` before confirming.

### Likelihood Explanation
Medium: it requires the user to have opted into Copilot conflict resolution and to be merging/rebasing a branch whose commit messages, PR context, or conflicting content is attacker-influenced (e.g., resolving a conflict against a malicious fork/PR branch), and it further requires the user not to carefully verify the diff before continuing. This is analogous to the market-condition dependency in the original report — plausible but not guaranteed on every use.

### Recommendation
Add a content-sanity check on `resolution.resolvedContent` before writing/staging in `_applyCopilotConflictResolutions` (and/or in `reassembleResolvedFile`): reject resolutions whose reassembled content still contains conflict-marker patterns (`<<<<<<<`, `=======`, `>>>>>>>` at line starts), and consider structural checks (e.g. balanced braces/parens for known languages, or a diff-size sanity bound relative to the original hunks) — falling back to manual resolution when the check fails, similar to how a swap would revert instead of accepting an out-of-bounds output.

### Proof of Concept
1. Prepare a repository/fork whose commit messages or PR description (fed into the Copilot prompt via `formatConflictContextForPrompt`) attempt to instruct/mislead the model.
2. Create a merge/rebase conflict between the victim's branch and this fork.
3. Trigger "Resolve with Copilot" (`_startCopilotConflictResolution` → `_resolveConflictsWithCopilot`).
4. If the model returns `resolvedContent` that still contains a subtle issue (or an unbalanced marker treated as literal text by the fallback path in `reassembleResolvedFile` lines 574-579), no validation rejects it.
5. User clicks "Continue Merge" (`onContinue` in `copilot-conflicts-dialog.tsx`), which calls `applyCopilotConflictResolutions` → writes the file and runs `git add` without further checks: [5](#0-4) [3](#0-2) .
6. The corrupted content is now staged and ready to be committed/pushed with no automated safeguard.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L473-521)
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

**File:** app/src/ui/multi-commit-operation/dialog/copilot-conflicts-dialog.tsx (L128-141)
```typescript
  private onContinue = async () => {
    this.setState({ isContinuing: true })
    try {
      // Write Copilot resolutions to disk before continuing the operation.
      // Done here (shared) so it works for merge, rebase, and cherry-pick.
      await this.props.dispatcher.applyCopilotConflictResolutions(
        this.props.repository
      )
      await this.props.onContinueAfterConflicts()
    } catch (e) {
      this.setState({ isContinuing: false })
      throw e
    }
  }
```
