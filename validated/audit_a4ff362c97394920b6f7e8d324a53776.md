### Title
Copilot conflict-resolution reassembly maps model hunk outputs to conflict markers by array order, not by content or count — allows silent corruption of merged file content - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The bug-class seed is Swivel's `lend()` failing to constrain which internal branch (`o.exit`/`o.vault`) an untrusted order can trigger, letting a value determined by attacker-influenced input drive accounting logic that the caller never validated matched the intended path. The Desktop analog is `reassembleResolvedFile()` in `app/src/lib/copilot-conflict-resolution.ts`, which splices Copilot's per-hunk `resolvedContent` entries into the on-disk file **purely by positional order**, with no check that the number of hunks in the model's response equals the number of actual conflict-marker blocks found in the file, nor any content correlation between a hunk and the block it replaces.

### Finding Description
`extractConflictHunks()` in `app/src/lib/copilot-conflict-context.ts:179-279` parses a conflicted file and builds an ordered array of hunks that is sent to the Copilot model as prompt content (`formatConflictContextForPrompt`, `app/src/lib/copilot-conflict-context.ts:482-594`). The model's JSON response is parsed by `parseCopilotConflictResolution` (`app/src/lib/copilot-conflict-resolution.ts:379-466`), which validates types/shape of each hunk's `resolvedContent` but never validates that `hunks.length` for a file equals the number of conflict blocks that actually exist in `rawContent` on disk.

That raw, unchecked array is then handed to `reassembleResolvedFile()` (`app/src/lib/copilot-conflict-resolution.ts:549-599`), which walks the original file's conflict-marker blocks in file order and replaces the *i*-th block with `hunkResolutions[i].resolvedContent` — matched "by order, not by line number" per the function's own doc comment (lines 535-536). There is no assertion that `hunkIndex` reaches `hunkResolutions.length - 1` exactly when the last marker block is consumed, and no verification that a given hunk's resolved content is even plausibly related to the `oursContent`/`theirsContent` of the block it is being spliced into.

This is structurally the same invariant violation as the Swivel finding: a downstream write/accounting operation trusts an attacker-influenceable, ordered/typed input to select which internal branch it takes, without the caller enforcing that the input matches the state it should correspond to.

### Impact Explanation
Because commit messages, PR titles, and PR bodies from both merge sides are fed verbatim into the same prompt as the conflict hunks (`appendPullRequest`, `app/src/lib/copilot-conflict-context.ts:600-610`, and the "Recent Commits" section, lines 503-522), an attacker who can get a commit or PR merged/rebased against by the victim (a collaborator, or the owner of a forked branch being merged) controls text that is concatenated into the same LLM turn used to produce the hunk-ordered JSON. If that influence causes the model to emit resolutions with a different hunk count/order than the actual on-disk conflict blocks (whether via a buggy model, a truncated/malformed response that still parses, or model steering via the untrusted commit/PR text), `reassembleResolvedFile` will silently attach the wrong resolved content to the wrong conflict block. The result is written straight to disk and `git add`-ed in `_applyCopilotConflictResolutions` (`app/src/lib/stores/app-store.ts:7169-7269`) with no diff-content sanity check tying the output back to the specific conflict it was supposed to resolve — this is exactly the "silent corruption of what the user commits" impact category, since the user sees a "resolved" file and stages/commits it without necessarily line-by-line re-verifying every hunk against the original conflict.

### Likelihood Explanation
Medium-low. The write path does have some mitigations: `resolveWithin` prevents path traversal (`app-store.ts:7233`), and files that were externally resolved are skipped (`app-store.ts:7247-7256`). But nothing constrains the *count or correspondence* of hunks returned by the model to the real number of markers, and the prompt intentionally includes attacker-reachable text (PR bodies/titles, commit summaries) alongside the hunk content the model is asked to resolve in order. This requires the attacker to have contributed a commit/PR that ends up as one side of a conflict the victim resolves with Copilot — not local access, not leaked credentials, and not any unnatural user action beyond the normal "Resolve with Copilot" flow the feature is designed to invite.

### Recommendation
In `reassembleResolutions`/`reassembleResolvedFile`, validate that `raw.hunks.length` exactly equals the number of conflict-marker blocks independently counted from `ctx.rawContent` before performing the splice, and throw a `CopilotValidationError` (falling back to marking the file skipped/needing manual resolution) on mismatch rather than silently truncating or leaving trailing hunks unused. Consider also tagging each hunk in the prompt/response with a stable index or a snippet of the original `oursContent`/`theirsContent` so the mapping can be verified against the file content, not just positional order.

### Proof of Concept
1. A collaborator opens a PR whose title/body is crafted to be included verbatim in the Copilot conflict-resolution prompt (`appendPullRequest`), and whose branch introduces multiple conflicting hunks against `main`.
2. Victim runs "Resolve with Copilot" during merge/rebase; `buildConflictContext` extracts, say, 3 real conflict hunks from the file and sends them plus the attacker's PR text to the model.
3. The model (influenced by the crafted PR context or simply due to non-determinism) returns a `resolutions[i].hunks` array with only 2 entries, or entries in a different order than the file's actual marker sequence — `parseCopilotConflictResolution` accepts this since it only validates shape, not count.
4. `reassembleResolvedFile` walks the 3 real marker blocks but only has 2 (or misordered) `hunkResolutions`; the third marker block is left unresolved/uses no content or the wrong resolved snippet is spliced into the wrong block.
5. `_applyCopilotConflictResolutions` writes this reassembled content to disk and stages it (`writeFile(absolutePath, resolution.resolvedContent, 'utf8')`, `git add`), with the user only shown the reassembled diff in the result dialog — a mismatch that is easy to miss on a multi-hunk file, leading to silently corrupted committed content.

<cite repo="blackvul/desktop--014" path="app/src/lib/copilot-conflict-resolution.ts" start="528="/> [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-642)
```typescript
export function reassembleResolutions(
  rawResolutions: ReadonlyArray<IRawFileResolution>,
  fileContexts: ReadonlyArray<IFileConflictContext>
): ReadonlyArray<IFileResolution> {
  const contextByPath = new Map(fileContexts.map(f => [f.path, f]))

  return rawResolutions.map(raw => {
    // Delete-vs-modify resolutions carry an action, not hunk content.
    // Pass through without reassembly — the resolution is applied as a
    // ManualConflictResolution, not a file write.
    if (raw.action !== undefined) {
      return {
        path: raw.path,
        resolvedContent: '',
        reasoning: raw.reasoning,
        deleteConflictAction: raw.action,
      }
    }

    const ctx = contextByPath.get(raw.path)
    if (ctx?.rawContent === undefined) {
      throw new CopilotValidationError(
        `Cannot reassemble resolution for "${raw.path}": original file content is unavailable`
      )
    }

    const resolvedContent = reassembleResolvedFile(ctx.rawContent, raw.hunks)
    return {
      path: raw.path,
      resolvedContent,
      reasoning: raw.reasoning,
    }
  })
}
```

**File:** app/src/lib/copilot-conflict-context.ts (L482-522)
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
```

**File:** app/src/lib/copilot-conflict-context.ts (L600-618)
```typescript
function appendPullRequest(
  parts: Array<string>,
  pr: IConflictContextPullRequest
): void {
  parts.push(`PR #${pr.number}: ${pr.title}`)
  if (pr.body) {
    parts.push('Description:')
    parts.push(makeFencedBlock(truncateBody(pr.body)))
  }
  parts.push('')
}

/** Truncate an over-long PR body so a single PR can't dominate the prompt. */
function truncateBody(body: string): string {
  if (body.length <= MAX_PR_BODY_LENGTH) {
    return body
  }
  return `${body.slice(0, MAX_PR_BODY_LENGTH)}\n…(truncated)`
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
