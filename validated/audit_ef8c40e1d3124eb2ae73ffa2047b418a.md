### Title
Copilot AI conflict-resolution payload is trusted positionally without validating it matches the original conflict hunks, risking silent commit/staging of wrong merge content - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
GitHub Desktop's "Resolve with Copilot" feature sends conflicted-file content to Copilot (a third-party/LLM API), receives back a JSON payload of per-hunk `resolvedContent` strings, reassembles them into full file text, and — once the user clicks "Continue Merge" — writes that text to disk and runs `git add` on it. The reassembly step blindly maps the model's `hunks` array onto the conflict markers found in the original file by position/order, with no check that the number, order, or origin of the returned hunks actually corresponds to the real conflict hunks that were extracted and sent. This mirrors the reported bug class: an opaque, externally-generated payload (`swapExtraData` in the original report, Copilot's JSON response here) is trusted to drive the final action (a token swap there, a file write/commit here) while the "explicit" structural inputs (source token/amount there, the actual conflict hunk boundaries here) are not cross-checked against what is executed.

### Finding Description
The flow is:
1. `buildConflictContext` extracts conflict hunks from each conflicted file and sends them to Copilot: [1](#0-0) 
2. Copilot's raw text response is parsed into `resolutions` (per-file, per-hunk) via `parseCopilotConflictResolution`: [2](#0-1) 
3. `reassembleResolutions` looks up the original file context strictly **by path** and then calls `reassembleResolvedFile(ctx.rawContent, raw.hunks)`, splicing the model's hunk resolutions into the original content: [3](#0-2) 
4. `_applyCopilotConflictResolutions` then writes the fully reassembled `resolution.resolvedContent` straight to disk at the resolved path and stages it with `git add`: [4](#0-3) 

The only integrity checks present are: the target path must stay inside the repository (`resolveWithin`), and a file that was manually resolved outside Desktop while the dialog was open is skipped. Neither of these validates that the **content** or **count/order of hunks** Copilot returned actually corresponds to the real conflict markers in that file. `reassembleResolvedFile` (tested only with matching hunk counts/order in the test suite: [5](#0-4) ) has no visible guard rejecting a response whose `hunks` array length or order diverges from the number of conflict markers actually present in `ctx.rawContent`. If the model (or a manipulated/compromised Copilot backend/response, or a repository whose conflict markers are adversarially crafted to confuse hunk extraction) returns a resolution set that is shorter, reordered, or otherwise misaligned with the true hunks, the splice will apply the wrong resolved text to the wrong conflict region, or leave stray content, while the UI still displays it as "Copilot's suggestion" for that hunk.

### Impact Explanation
This can silently corrupt what the user commits/pushes — the resolved file written to disk and staged for commit may not reflect either "ours" or "theirs" as intended, nor what the dialog claims to show, without any diff-integrity check tying the applied text back to the specific conflict hunk it was generated for. Because the write happens automatically once the user clicks "Continue Merge" (a single confirmation, not a hunk-by-hunk diff review against the AI's literal patch), a subtly wrong reassembly could go unnoticed and be committed/pushed, potentially reintroducing security-relevant code that was supposed to be replaced, or silently dropping a fix that was supposed to be kept.

### Likelihood Explanation
Likelihood is moderate-to-low: it depends on Copilot's response format drifting from the expected 1:1, in-order hunk correspondence (e.g., due to model non-determinism on files with many overlapping conflict hunks, or a compromised/malicious API endpoint response if BYOK/custom providers are used — `resolveCopilotModelRequest`/`byokProviders` referenced in `app-store.ts`). This requires no local access, admin rights, or social engineering beyond the user clicking the already-present "Resolve with Copilot" → "Continue Merge" buttons on a repository containing crafted conflicting content, which fits the "attacker controls a cloned/fetched repository" impact category.

### Recommendation
Add integrity checks in `reassembleResolutions`/`reassembleResolvedFile` that:
- Reject a resolution set whose hunk count does not match the number of conflict markers actually extracted from the file.
- Verify each hunk's resolved content is associated with the specific marker boundaries it targets (e.g., by echoing back a hunk index/anchor from the original context and validating it on reassembly) rather than relying purely on array order.
- Fail closed (mark the file as `skippedReason`) rather than best-effort splicing when hunk metadata cannot be reconciled.

### Proof of Concept
Not independently exploitable from static review alone since it depends on the runtime shape of Copilot API responses; conceptually:
1. Set up a repository with a merge/rebase producing multiple conflict hunks in one file.
2. Trigger "Resolve with Copilot" (`attemptCopilotConflictResolution` → `_startCopilotConflictResolution`) against a Copilot backend/response that returns a `resolutions[].hunks` array with a different count or order than the real conflicts (achievable via a custom/BYOK provider endpoint returning a crafted JSON body matching `parseCopilotConflictResolution`'s expected schema).
3. Observe that `reassembleResolvedFile` splices the mismatched resolutions into the file without any validation, then `_applyCopilotConflictResolutions` writes and `git add`s the result once "Continue Merge" is clicked. [4](#0-3) 

This is uncertain because the exact body of `reassembleResolvedFile` (matching logic beyond what's shown in tests) was not fully retrievable within the available context; a Devin session with full file access would be needed to confirm whether any hidden length/order check exists before concluding this is exploitable as described.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L440-447)
```typescript
      const hunks = extractConflictHunks(content)
      if (hunks.length === 0) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'No conflict markers found',
        }
      }
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L281-340)
```typescript
export function parseCopilotConflictResolution(
  content: string
): ICopilotConflictResolutionResponse {
  // Build a list of JSON candidates from the response, trying different
  // extraction strategies. Non-greedy handles the common single-block and
  // multi-block cases. Greedy handles triple backticks embedded inside JSON
  // content. Raw content handles responses with no fences at all.
  const nonGreedy =
    content.match(/```json\s*([\s\S]*?)```/) ||
    content.match(/```\s*([\s\S]*?)```/)
  const greedy =
    content.match(/```json\s*([\s\S]*)```/) ||
    content.match(/```\s*([\s\S]*)```/)

  const candidates: Array<string> = []
  if (nonGreedy) {
    candidates.push(nonGreedy[1].trim())
  }
  if (greedy && greedy[1].trim() !== nonGreedy?.[1]?.trim()) {
    candidates.push(greedy[1].trim())
  }
  candidates.push(content.trim())

  let parsed: unknown
  let parseError: Error | undefined
  for (const candidate of candidates) {
    try {
      parsed = JSON.parse(candidate)
      parseError = undefined
      break
    } catch {
      parseError = new CopilotValidationError(
        'Copilot returned invalid JSON for conflict resolution generation'
      )
    }
  }
  if (parseError) {
    throw parseError
  }

  if (!isPlainObject(parsed)) {
    throw new CopilotValidationError(
      'Copilot returned an invalid conflict resolution payload: expected an object'
    )
  }

  const obj = parsed as Record<string, unknown>
  const { resolutions, summary: rawSummary, references: rawReferences } = obj

  if (!Array.isArray(resolutions)) {
    throw new CopilotValidationError(
      'Copilot returned an invalid conflict resolution payload: "resolutions" must be an array'
    )
  }

  if (resolutions.length === 0) {
    throw new CopilotValidationError(
      'Copilot returned an invalid conflict resolution payload: "resolutions" must not be empty'
    )
  }
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L609-641)
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

**File:** app/test/unit/copilot-conflict-resolution-test.ts (L450-500)
```typescript
describe('reassembleResolvedFile', () => {
  it('replaces a single conflict in the middle of a file', () => {
    const raw = [
      'line 1',
      'line 2',
      '<<<<<<< HEAD',
      'our change',
      '=======',
      'their change',
      '>>>>>>> feature',
      'line 3',
      'line 4',
    ].join('\n')

    const result = reassembleResolvedFile(raw, [
      { resolvedContent: 'merged change' },
    ])

    assert.equal(
      result,
      ['line 1', 'line 2', 'merged change', 'line 3', 'line 4'].join('\n')
    )
  })

  it('replaces multiple conflicts in order', () => {
    const raw = [
      'header',
      '<<<<<<< HEAD',
      'our-1',
      '=======',
      'their-1',
      '>>>>>>> feature',
      'middle',
      '<<<<<<< HEAD',
      'our-2',
      '=======',
      'their-2',
      '>>>>>>> feature',
      'footer',
    ].join('\n')

    const result = reassembleResolvedFile(raw, [
      { resolvedContent: 'resolved-1' },
      { resolvedContent: 'resolved-2' },
    ])

    assert.equal(
      result,
      ['header', 'resolved-1', 'middle', 'resolved-2', 'footer'].join('\n')
    )
  })
```
