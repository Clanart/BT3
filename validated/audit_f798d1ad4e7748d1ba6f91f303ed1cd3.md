## Analog Found

### Title
Untrusted PR/commit content is injected unsanitized into the Copilot conflict-resolution prompt, and order-only hunk validation lets a manipulated response silently mis-splice resolved content into the wrong conflict block - (File: `app/src/lib/copilot-conflict-resolution.ts`, `app/src/lib/copilot-conflict-context.ts`)

### Summary
The MetaVesT report's core defect is that two values (governance power contributions) with different implicit "scale" (decimals) are combined by a calculation that assumes a common unit, silently producing wrong results. The same broken-invariant class exists in Desktop's Copilot conflict-resolution feature: `reassembleResolvedFile` combines the model's per-hunk resolutions with the on-disk conflict markers **purely by positional order**, and the only integrity check (`validateResolutionPaths`) verifies just the *count* of hunks matches, never that resolution *N* actually corresponds to conflict block *N*. The values entering the model's context - PR titles/bodies and commit summaries - are attacker-influenceable GitHub API objects that are inserted into the prompt with no content sanitization (only length truncation and path sanitization). This lets a remote actor (an untrusted contributor whose PR/commit metadata gets pulled into conflict context) attempt to bias or reorder the model's structured output, and because the app only checks hunk *counts*, a swap or reorder passes validation and gets spliced into the wrong location in the user's file — corrupting what the user ultimately commits, without any error or warning.

### Finding Description
`buildConflictContext`/`formatConflictContextForPrompt` in `app/src/lib/copilot-conflict-context.ts` place PR titles, PR bodies, and commit summaries directly into the prompt text sent to Copilot: [1](#0-0) 

Only `sanitizeForMarkdown` (used for file paths) strips control characters; PR bodies and commit summaries are truncated but never sanitized against embedded instructions: [2](#0-1) 

On the response side, `reassembleResolvedFile` explicitly documents that it matches resolutions "by order, not by line number": [3](#0-2) 

and its implementation splices `hunkResolutions[hunkIndex]` into whatever conflict block it encounters next, with no check that the content actually belongs there: [4](#0-3) 

The only guard, `validateResolutionPaths`, checks solely that the *number* of returned hunks equals the *number* of expected hunks per file — it does not verify hunk identity, content correspondence, or ordering: [5](#0-4) 

This is structurally the same defect as the MetaVesT bug: two independently derived sequences (extracted conflict hunks vs. model-returned resolutions) are combined by raw positional index while assuming they are homogeneous/aligned, with validation only checking a coarse aggregate (hunk count / total governance amount) rather than per-element correspondence (per-hunk identity / per-MetaVesT decimals).

### Impact Explanation
If the ordering or count-preserving-but-misassigned output can be induced (via prompt content the attacker controls, e.g. a malicious PR title/body or commit message baked into the same conflict-resolution prompt), the resolved content for one conflict hunk gets silently written into a different hunk's location in the target file. Because `validateResolutionPaths` only checks the hunk count, this passes validation, gets written to disk, and can be committed by the user without any warning — a silent corruption of what the user commits, which is explicitly a qualifying impact.

### Likelihood Explanation
Requires an unprivileged actor who can influence commit messages or PR title/body reachable by the target repository's merge/rebase/cherry-pick conflict context (e.g., a contributor's PR against the repo, or commits on a branch being merged) — no local access, no elevated privileges, and no prior compromise of the host are required. The lack of content sanitization on PR body/commit summary text (only truncation) and the order-only reassembly/validation logic together make this a plausible, reachable path whenever Desktop's AI-assisted conflict resolution is used on a merge/rebase involving attacker-influenced branches.

### Recommendation
- Sanitize/neutralize PR body and commit summary text before embedding it in the Copilot prompt (e.g., strip or escape markdown/instruction-like sequences), not just truncate it.
- Strengthen `validateResolutionPaths` (and/or `reassembleResolvedFile`) to bind each resolution to its specific conflict block by content fingerprint or explicit hunk identifier rather than by array order/count alone, so a reordered or mismatched response is rejected instead of silently spliced.

### Proof of Concept
1. Attacker opens a PR (or pushes commits) against the target repository with a PR body/commit message containing content designed to influence the model's structured JSON output order (e.g., instructing it to reorder the `hunks` array while keeping the same length).
2. A user merges/rebases against this branch, encounters a merge conflict with two or more hunks in the same file, and runs Desktop's Copilot-assisted conflict resolution.
3. `buildConflictContext`/`formatConflictContextForPrompt` include the attacker's PR body/commit summary verbatim (only truncated) in the prompt sent to Copilot.
4. Copilot returns a `hunks` array with the correct count but swapped/reordered `resolvedContent` entries.
5. `validateResolutionPaths` passes because `resolution.hunks.length === expectedCount` at `app/src/lib/copilot-conflict-resolution.ts:514-519`.
6. `reassembleResolvedFile` splices `hunkResolutions[0]` into the first conflict marker block and `hunkResolutions[1]` into the second regardless of which one it was actually meant for, per `app/src/lib/copilot-conflict-resolution.ts:584-591`, silently producing a file whose resolved content does not match either side's actual conflict resolution intent — which the user may then commit unknowingly.

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L503-522)
```typescript
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

**File:** app/src/lib/copilot-conflict-resolution.ts (L529-548)
```typescript
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
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L559-599)
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

  return resultLines.join(eol)
}
```
