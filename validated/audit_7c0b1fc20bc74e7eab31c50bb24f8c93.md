## Title
Copilot conflict-resolution writes model-supplied file paths without verifying they belong to the conflicted-file set sent for resolution - (File: `app/src/lib/stores/app-store.ts`)

## Summary
The Copilot-assisted merge-conflict resolution feature sends a bounded list of conflicted files/hunks to an AI model and later writes the model's returned `resolutions` back to disk. The disk write is guarded against path traversal (`resolveWithin`), but I could not confirm — and the code I was able to inspect does not show — that `resolution.path` values returned by the model are validated against the actual list of files (`conflictedFiles`/`context.files`) that were sent to it before being written into the working directory.

## Finding Description
`AppStore._acceptCopilotConflictResolutions` (around `app/src/lib/stores/app-store.ts:7171-7269`) iterates `copilotResolutions` and, for each entry, resolves the path with `resolveWithin(repository.path, resolution.path)` [1](#0-0)  and then writes `resolution.resolvedContent` to that path with `writeFile(absolutePath, resolution.resolvedContent, 'utf8')` [2](#0-1) . The only existing-file check that gates the write is a *skip* condition — if `onDiskFile` is found, is a conflicted-status file, and its conflicts are already resolved externally, the write is skipped [3](#0-2) . If `onDiskFile` is `undefined` (i.e., the model returned a `path` that is *not* one of the files sent to it as a conflict), the `&&`-chained guard short-circuits to `false` and the code falls through to `writeFile` anyway.

The `resolution.path` values ultimately originate from the model's JSON response (`ConflictResolutionSystemPrompt`, `app/src/lib/copilot-conflict-resolution.ts:218-241`), and the file/hunk content the model is fed is read from the attacker-influenced repository content itself (commit messages, PR bodies, and file conflict text — see `formatConflictContextForPrompt`) [4](#0-3) . This is the classic "unauthorized mint/write primitive" pattern from the seed report: a privileged write operation (`writeFile` into the user's working tree) is performed based on attacker/model-controlled input (`resolution.path`) without confirming the target is one of the entities the operation was actually authorized to act on (the conflicted files list built by `getConflictedFiles`, `app/src/lib/stores/app-store.ts:6549-6552`).

I was not able to fully trace whether `resolveChunk`/the JSON parser in `app/src/lib/copilot-conflict-resolution.ts` cross-checks `resolutions[].path` against the input file list before returning `IFileResolution[]` to the caller — my last grep for `resolutions.filter`/`validResolutions` found only 3 total matches across the codebase and I ran out of iterations before reading `copilot-resolution-helpers.ts` and the relevant section of `copilot-conflict-resolution.ts` in full. **This is the key open question**: if such validation exists in the parsing layer, this finding is a non-issue (defense-in-depth already present, matching the same hardening pattern seen elsewhere in this codebase — `resolveWithin`, `sanitizeCloneName`, `isTrustedIPCSender`, `isClonePathSensitive`). If it does not exist, the missing authorization check at the `_acceptCopilotConflictResolutions` call site is directly exploitable.

## Impact Explanation
If the path-membership check is missing, a maliciously crafted repository (e.g., a PR description or commit message containing a prompt-injection payload, combined with the fact that repository content is fed verbatim into the model prompt) could cause the model to emit a `resolutions[].path` pointing at an arbitrary file within the repository working directory that is unrelated to any actual conflict — silently overwriting tracked files the user did not intend to touch, corrupting what they subsequently `git add`/commit/push. Because `resolveWithin` bounds the write to the repository root, this would not escape the repo, but within the repo it is a silent-corruption-of-committed-content primitive, matching the "silent corruption of what the user commits or pushes" impact class explicitly called out as valid.

## Likelihood Explanation
This requires the Copilot conflict-resolution feature to be enabled and the model to actually emit an out-of-scope path — either through prompt injection embedded in repository content (PR/commit text is fed into the prompt per `formatConflictContextForPrompt`) or non-adversarial model hallucination. It does not require local/physical access, admin rights, leaked credentials, or unnatural user steps — merely that the user runs the (opt-in) AI conflict resolution feature on a repository containing attacker-influenced text (e.g., a PR from an external contributor). Likelihood is speculative without confirming the absence of a membership check in the JSON-parsing layer, which I could not verify in the time available.

## Recommendation
In `AppStore._acceptCopilotConflictResolutions` (or earlier, in the response-parsing layer in `copilot-conflict-resolution.ts`), explicitly filter/validate `copilotResolutions` against the set of `conflictedFiles` paths that were actually sent to the model before ever reaching `resolveWithin`/`writeFile`. Reject (and log) any resolution whose `path` does not match an entry in the original conflicted-file list, rather than relying solely on the incidental `onDiskFile` lookup used for the "already resolved externally" skip check.

## Proof of Concept
Not independently reproducible from the index alone — a full PoC would require: (1) confirming there is no path-membership filter between `copilotStore.resolveConflicts` and `_acceptCopilotConflictResolutions`, and (2) constructing a repository whose PR/commit text causes the model to return a `resolutions[].path` for a file outside `conflictedFiles` (e.g., `.github/workflows/ci.yml` or a source file with no conflict markers) while still being within the repo, then confirming `_acceptCopilotConflictResolutions` writes to it. I could not execute this due to reaching the tool-call limit before inspecting `app/src/lib/copilot-resolution-helpers.ts` and the full body of `app/src/lib/copilot-conflict-resolution.ts`. **I recommend starting a Devin session with filesystem access to confirm whether this validation gap actually exists before treating this as a confirmed finding.**

### Citations

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/lib/stores/app-store.ts (L7247-7256)
```typescript
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

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```

**File:** app/src/lib/copilot-conflict-context.ts (L482-525)
```typescript
export function formatConflictContextForPrompt(
  context: IConflictResolutionContext
): string {
  const parts: Array<string> = []

  parts.push(
    `Merge conflict between "${context.ourLabel}" (ours) and "${context.theirLabel}" (theirs).`
  )
  parts.push('')

  if (context.pullRequests.length > 0) {
    parts.push('## Pull Request Context')
    parts.push(
      'These pull requests were referenced in the commit history and may explain the intent behind either side:'
    )
    parts.push('')
    for (const pr of context.pullRequests) {
      appendPullRequest(parts, pr)
    }
  }

  if (context.ourCommits.length > 0 || context.theirCommits.length > 0) {
    parts.push('## Recent Commits')
    parts.push('')

    if (context.ourCommits.length > 0) {
      parts.push(`### Ours (${context.ourLabel}) commits:`)
      for (const commit of context.ourCommits) {
        parts.push(`- ${commit.shortSha}: ${commit.summary}`)
      }
      parts.push('')
    }

    if (context.theirCommits.length > 0) {
      parts.push(`### Theirs (${context.theirLabel}) commits:`)
      for (const commit of context.theirCommits) {
        parts.push(`- ${commit.shortSha}: ${commit.summary}`)
      }
      parts.push('')
    }
  }

  for (const file of context.files) {
    const safePath = sanitizeForMarkdown(file.path)
```
