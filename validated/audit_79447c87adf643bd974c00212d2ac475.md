### Title
Copilot conflict-resolution reassembly splices AI hunks by position only, allowing prompt-injected content to silently corrupt committed file content - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The external report's core flaw is a loop that fails to track *which* item it is actually operating on (index never advances, so processing silently drifts onto the wrong element while bookkeeping still looks correct). The closest verifiable analog in this Desktop codebase is `reassembleResolvedFile` / `reassembleResolutions` in `app/src/lib/copilot-conflict-resolution.ts`: resolved hunks from an AI ("Copilot") conflict-resolution response are spliced into the on-disk file **purely by array order/position**, and the only integrity check (`validateResolutionPaths`) verifies a **count**, not an identity/content binding between a hunk and the specific conflict block it is meant to replace.

### Finding Description
`buildConflictContext` (`app/src/lib/copilot-conflict-context.ts:367-469`) reads each conflicted file (content that originates from an attacker-influenced merge/rebase — i.e., a fetched/cloned branch, PR, or remote ref the user is merging) and extracts conflict hunks with `extractConflictHunks` [1](#0-0) . This context, including raw commit messages and PR bodies from both sides, is serialized into a prompt sent to the Copilot model [2](#0-1) .

The model's JSON response is parsed by `parseCopilotConflictResolution`, which validates JSON shape but not resolvedContent semantics, and only rejects a hunk if it still contains literal `<<<<<<<`/`=======` markers [3](#0-2) . `validateResolutionPaths` then checks only that the **number** of returned hunks per file matches the number originally extracted — it never verifies that hunk *i* in the model's response corresponds to conflict block *i* by content or anchor [4](#0-3) .

Finally, `reassembleResolvedFile` walks the **same raw file text again**, independently re-locating `<<<<<<<...=======...>>>>>>>` blocks with its own separate regex-based scanner, and splices `hunkResolutions[hunkIndex]` into place using nothing but a running counter (`hunkIndex`), matched "by order, not by line number" (explicit in-code comment) [5](#0-4) .

This is the direct structural analog of the `_depositEther` bug: correctness depends entirely on an implicit index/position invariant holding between two independently-executed passes (context-extraction ordering vs. reassembly-scan ordering, and the model's output ordering), with no cross-check that the *n*-th resolution actually belongs to the *n*-th conflict region. Because the full commit-message/PR-body content of an attacker-controlled branch or PR is fed verbatim into the prompt (`formatConflictContextForPrompt`), an attacker who controls the incoming ("theirs") branch/PR can embed prompt-injection instructions in commit summaries, PR body text, or even conflicted file content itself, steering the model to emit a hunk count that matches the validator's expectation while ordering/labelling the resolvedContent so that malicious code lands in a hunk position the user did not intend to alter, or so that unrelated hunks get overridden together. Because reassembly is order-based, only the count invariant is checked, and no identity/content anchor ties a resolution to its source block, the final `resolvedContent` written to disk (and subsequently committed by the user) can silently diverge from an honest merge of the visible conflict UI.

### Impact Explanation
If the user accepts the AI's summarized resolution without carefully diffing every hunk against the original conflict markers, attacker-controlled/prompt-injected content is written verbatim into the working file and can be committed and pushed without any tooling alarm — this is exactly the "silent corruption of what the user commits" category. The corrupted content is written directly into the repository working tree (`reassembleResolutions` → file write path in `copilot-store.ts`), so it can introduce backdoored code, altered dependency manifests, or other planted logic that ships as if it were a normal AI-assisted merge resolution.

### Likelihood Explanation
Exploitation requires: (1) the user to be merging/rebasing/cherry-picking a branch, PR, or remote ref that the attacker controls or has contributed to (a common, unprivileged workflow — reviewing/merging external contributions), and (2) the user to invoke the Copilot conflict-resolution feature and accept its output without manually re-verifying every hunk against the raw conflict markers. Because prompt-injection reliability against LLMs is probabilistic and the exact splicing behavior depends on subtle interactions between two independently-implemented marker scanners, this is not a fully deterministic, always-reproducible exploit chain from local static analysis alone — I was not able to trace the full acceptance-dialog UI (no `CopilotConflictResolutionDialog`/`applyResolution` symbol was found in the index) to confirm whether the UI presents a diff review step that a user would need to bypass, which affects how easily a malicious hunk would be caught before commit. This uncertainty is due to index/search coverage limits, not to a ruled-out path.

### Recommendation
- Bind each hunk resolution to its source conflict block by content anchor (e.g., hash of the original ours/base/theirs text, or explicit line-range echoing) rather than positional order alone, and reject/flag any resolution whose anchor doesn't match during reassembly.
- Make `reassembleResolvedFile` reuse the exact same parsing routine as `extractConflictHunks` (single source of truth for marker/hunk boundaries) instead of maintaining a second, independently-drifting scanner.
- Treat commit messages/PR bodies/file content from the "theirs" side as untrusted input to the LLM prompt; apply prompt-injection mitigations (delimiting/escaping untrusted text, instructing the model to ignore embedded instructions) before including them in `formatConflictContextForPrompt`.
- Require an explicit user-facing diff review (highlighting exactly which lines the AI changed) before writing `resolvedContent` to disk, rather than only surfacing a textual summary/reasoning field.

### Proof of Concept
Full end-to-end reproduction was not possible from static code review alone since it depends on (a) the live behavior of the Copilot model under a crafted prompt-injection payload and (b) the resolution-acceptance UI flow, which could not be located in the indexed codebase. The verifiable, code-level evidence is the design flaw itself:
1. Create a repository conflict where the incoming ("theirs") side is attacker-controlled and its commit summary/PR body contains prompt-injection text (fed into `formatConflictContextForPrompt`, `app/src/lib/copilot-conflict-context.ts:482-594`).
2. Trigger Copilot conflict resolution; the model returns a `resolutions[].hunks[]` array whose length matches `expectedHunkCounts` (satisfying `validateResolutionPaths`, `app/src/lib/copilot-conflict-resolution.ts:473-521`) but whose `resolvedContent` values are attacker-influenced.
3. `reassembleResolvedFile` (`app/src/lib/copilot-conflict-resolution.ts:549-599`) splices these values into the file purely by the `hunkIndex` counter, with no check that resolution *i* matches conflict block *i*'s actual original content.
4. The resulting `resolvedContent` is written to disk and, if the user commits without line-by-line review, ships attacker-chosen content silently.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L179-243)
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

**File:** app/src/lib/copilot-conflict-context.ts (L482-594)
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

    if (file.deleteConflict) {
      const { deletedSide } = file.deleteConflict
      const deletedLabel =
        deletedSide === 'ours' ? context.ourLabel : context.theirLabel
      const modifiedLabel =
        deletedSide === 'ours' ? context.theirLabel : context.ourLabel

      parts.push(`## File: ${safePath} (delete-vs-modify conflict)`)
      parts.push('')
      parts.push(
        `Deleted on "${deletedLabel}" (${deletedSide}), modified on "${modifiedLabel}" (${
          deletedSide === 'ours' ? 'theirs' : 'ours'
        }).`
      )
      parts.push('')
      parts.push(
        'Respond with `"action": "keep"` to preserve the modified file, or `"action": "delete"` to accept the deletion.'
      )
      parts.push('')
      continue
    }

    parts.push(`## File: ${safePath}`)
    parts.push('')

    if (file.skippedReason) {
      parts.push(`> ⚠️ Skipped: ${file.skippedReason}`)
      parts.push('')
      continue
    }

    const lang = getLangFromPath(file.path)

    for (let i = 0; i < file.hunks.length; i++) {
      const hunk = file.hunks[i]
      parts.push(`### Conflict ${i + 1} of ${file.hunks.length}`)
      parts.push('')

      if (hunk.contextBefore) {
        parts.push('Context before:')
        parts.push(makeFencedBlock(hunk.contextBefore, lang))
        parts.push('')
      }

      parts.push('Ours (current branch):')
      parts.push(makeFencedBlock(hunk.oursContent, lang))
      parts.push('')

      if (hunk.baseContent !== null) {
        parts.push('Base (common ancestor):')
        parts.push(makeFencedBlock(hunk.baseContent, lang))
        parts.push('')
      }

      parts.push('Theirs (incoming branch):')
      parts.push(makeFencedBlock(hunk.theirsContent, lang))
      parts.push('')

      if (hunk.contextAfter) {
        parts.push('Context after:')
        parts.push(makeFencedBlock(hunk.contextAfter, lang))
        parts.push('')
      }
    }
  }

  return parts.join('\n')
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L429-450)
```typescript
    const validatedHunks: Array<IHunkResolution> = []
    for (let j = 0; j < rawHunks.length; j++) {
      const hunkEntry: unknown = rawHunks[j]
      if (!isPlainObject(hunkEntry)) {
        throw new CopilotValidationError(
          `Copilot returned an invalid conflict resolution payload: hunk at index ${j} of file "${path}" must be an object`
        )
      }
      const hunkObj = hunkEntry as Record<string, unknown>
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L528-599)
```typescript
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
