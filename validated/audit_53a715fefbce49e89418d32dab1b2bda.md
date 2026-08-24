## Analog Identified: Copilot conflict resolution reassembly matches hunks by array position, not by identity

### Title
Copilot-resolved conflict hunks are spliced back into files by list order, not by unique identity, allowing prompt-injected/attacker-controlled conflict content to silently corrupt merge results - (File: app/src/lib/copilot-conflict-resolution.ts)

### Summary
The underlying bug class in the report is that events lacking a unique identifier let a consumer misattribute one event's payload to another slot, causing silent double-processing/corruption. The closest verified analog in GitHub Desktop is in `reassembleResolvedFile`, which stitches Copilot's LLM-generated conflict resolutions back into a file using only positional order (`hunkIndex`), never a content- or location-based identifier tying a specific resolution to the specific conflict block it was generated for.

### Finding Description
When the user asks Copilot to resolve merge/rebase/cherry-pick conflicts, Desktop sends the raw conflicted file content (which originates from the working tree, i.e. from branches/commits that can come from an attacker-controlled clone or fetched remote) to the model, and the model returns a JSON array of `hunks`, each just `{ resolvedContent }` with no identifier tying it to a specific conflict marker block [1](#0-0) .

The only integrity check performed is `validateResolutionPaths`, which verifies that the returned path set matches expected paths and that the *count* of hunks per file matches the expected conflict count for that file [2](#0-1) . It does not verify that hunk `j` actually corresponds to conflict block `j` in the original file — there is no marker echoing, hash, or line-range identifier in the model's response that Desktop can use to confirm correspondence.

`reassembleResolvedFile` then walks the raw file top-to-bottom, and for every conflict marker block it encounters it simply consumes the next element of `hunkResolutions` in array order: `hunkIndex++` and `hunkResolutions[hunkIndex].resolvedContent` [3](#0-2) . If the model returns the right number of hunks for a file but in a different order than the conflicts appear on disk (e.g. due to prompt-injected instructions embedded in a conflicting file's content, or the model simply reordering hunks in its structured output), the count-based guard in `validateResolutionPaths` passes, yet the wrong resolved content is silently spliced into the wrong conflict location.

This corrupted content is then written straight to disk and staged for commit with no additional review step forcing the user to compare it against the original conflict: [4](#0-3) 

There is a check to avoid clobbering conflicts the user manually resolved outside Desktop [5](#0-4) , but nothing detects a same-count-but-wrong-order hunk mismatch — that class of corruption is invisible to all existing guards.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes." Since the conflicting content that seeds the LLM prompt comes directly from the working tree — which can contain attacker-authored content merged in from a malicious fork/branch/PR the user is resolving conflicts against — an attacker who can influence the LLM's hunk ordering (via prompt-injection-style content in the conflicting hunks, e.g., text designed to bias the model's structured output ordering) can cause Desktop to commit content the user never reviewed as being associated with that specific conflict. The user sees the diff for the correct number of hunks/files and believes it's correct, but the actual line-level attribution is wrong, silently corrupting the resulting commit that gets pushed.

### Likelihood Explanation
Likelihood is moderate: it requires (1) the user to opt into the "Resolve with Copilot" AI feature, and (2) the attacker's malicious branch/fork to actually influence the model's hunk ordering while keeping the hunk count correct (a subtle failure mode of LLM structured output, more plausible than getting the model to intentionally desync counts, which is explicitly checked). It does not require local access, admin rights, or leaked credentials — only that the user resolves conflicts against attacker-influenced content, which is a normal Desktop workflow (reviewing/merging a fork's PR).

### Recommendation
Add a positive identifier binding each hunk resolution to the specific conflict block it replaces — e.g., have the prompt/context include a stable per-hunk index or content fingerprint (such as a short hash of the original `ours`/`theirs` block) and require the model to echo it back in each hunk entry. `reassembleResolvedFile`/`reassembleResolutions` should then validate that identifier against the actual conflict block before splicing, rejecting (or falling back to manual resolution for) any hunk whose identifier doesn't match the block it's about to replace, rather than trusting array order alone.

### Proof of Concept
1. Set up a repository conflict where a file has 2 conflict blocks, A and B, with distinguishable content.
2. Craft the "theirs" side of the merge (attacker-controlled fork/branch content) to include text near/inside the conflict hunks designed to bias the LLM's JSON generation order (e.g., instructive text suggesting it explain/resolve conflict B before conflict A in its output, while `hunks.length` still equals 2).
3. Trigger `_startCopilotConflictResolution` → `parseCopilotConflictResolution` → `validateResolutionPaths` (passes because hunk count = 2, matching expected) → `reassembleResolutions`/`reassembleResolvedFile`.
4. Because splicing is purely positional (`hunkIndex`), resolution intended for conflict B is written into conflict A's location and vice versa.
5. User clicks "Continue Merge," `_applyCopilotConflictResolutions` writes the corrupted file to disk and stages it via `git add`, producing a commit with silently swapped/incorrect merged content. [6](#0-5) [2](#0-1)

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L225-245)
```typescript
  "resolutions": [
    {
      "path": "relative/file/path.ts",
      "hunks": [
        { "resolvedContent": "merged content that replaces conflict 1" },
        { "resolvedContent": "merged content that replaces conflict 2" }
      ],
      "reasoning": "What each side changed in this file, what you kept, and what you dropped or overrode."
    },
    {
      "path": "deleted-or-modified/file.ts",
      "action": "keep",
      "hunks": [],
      "reasoning": "The file was modified with important changes; the deletion was part of an incomplete refactor."
    }
  ]
}

Field rules:

hunks: An ordered array with one entry per conflict in the file, matching the "Conflict 1 of N", "Conflict 2 of N" order from the input. Each entry's resolvedContent is ONLY the merged content that replaces that specific conflict marker block (the region between <<<<<<< and >>>>>>>). Do NOT include surrounding non-conflicted code — the application splices each resolution into the original file automatically. If the resolution is to accept one side entirely, return that side's content verbatim. For an intentional deletion, use an empty string. For delete-vs-modify conflicts, hunks must be an empty array.
```

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

**File:** app/src/lib/stores/app-store.ts (L7233-7267)
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
```
