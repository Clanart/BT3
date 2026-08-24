### Title
Order-based (not content-verified) hunk matching lets an attacker-controlled merge/PR/commit content cause resolved conflict text to be silently spliced into the wrong location - ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
`reassembleResolvedFile()` and `validateResolutionPaths()` in `app/src/lib/copilot-conflict-resolution.ts` combine the Copilot model's per-hunk resolutions with the original file content purely by **positional order and count**, never by an identifier tying a resolution back to the specific conflict block it was generated for. The only server-side check is that the *number* of returned hunks equals the *number* of hunks detected by `extractConflictHunks()`. This is structurally the same invariant failure as the audited `KPITokensManager.removeTemplate()` bug: an index/position is trusted to reference the correct item, but nothing enforces that the position still corresponds to the same logical entity after the surrounding data has been manipulated.

### Finding Description
The prompt built from repository content is untrusted: `buildConflictContext()` (in `app/src/lib/copilot-conflict-context.ts`, lines 376-461) feeds file conflict markers, and the surrounding `IConflictResolutionContext` includes commit summaries and PR titles/bodies pulled from both sides of the merge — all attacker-influenceable content when the "theirs" side is a fetched branch, a PR, or a cherry-picked/rebased commit authored by someone else.

The model is asked to return, per file, `hunks: [{ resolvedContent }, ...]` "matching the Conflict 1 of N, Conflict 2 of N order from the input" (system prompt, lines 190-254). Downstream:

- `validateResolutionPaths()` (lines 473-521) only checks: (a) the returned path set matches expected paths, (b) no duplicate paths, (c) `resolution.hunks.length === expectedHunkCounts.get(path)`. It never checks that hunk *content* corresponds to the specific conflict block it targets. [1](#0-0) 

- `reassembleResolvedFile()` (lines 549-599) walks the raw file, and for the k-th conflict marker block found textually, splices in `hunkResolutions[hunkIndex]` — purely by counting order, with no cross-check against the hunk's `oursContent`/`theirsContent` recorded during context extraction. [2](#0-1) 

Because matching is "by order, not by line number" (explicitly documented at lines 535-536) and the only invariant enforced is a **count**, not an **identity/index binding**, any manipulation that causes the model to return the right *number* of hunks for a file but in the wrong order, or with content intended for a different conflict block, passes validation silently and gets spliced into an unrelated location in the file. This mirrors the report's root cause exactly: `templateIdToIndex[_lastTemplate.id] = _index` was trusted after the underlying item moved, without re-validating identity — here, hunk position is trusted after the underlying semantic mapping (which resolution belongs to which marker block) can be desynchronized via attacker-controlled prompt content (multi-hunk files, adversarial commit/PR text encouraging reordering, or ambiguous instructions embedded in code comments/commit messages — classic prompt injection).

### Impact Explanation
If a file has multiple conflict hunks and the model's returned resolutions are reordered or mismatched by only one position, `reassembleResolvedFile` will silently substitute the wrong merged content into a conflict region while keeping the file's overall structure and hunk count intact. Because the resulting diff is generally plausible-looking code (both `ours` and `theirs` are real code paths for the file), a user relying on the AI-assisted resolution flow could commit and push code where a resolution intended for one conflicting region (e.g., a security check hunk) is applied to a different region, or vice versa — silently corrupting what the user commits/pushes, without any error surfaced by `validateResolutionPaths`. This falls squarely in the "silent corruption of what the user commits or pushes" impact category from an attacker-controlled repository/PR/branch content, without requiring local access, credentials, or social engineering beyond a routine merge/rebase/cherry-pick against attacker-authored history.

### Likelihood Explanation
Exploitation requires: (1) a repository with multiple conflict hunks in at least one file during merge/rebase/cherry-pick against attacker-influenced content (a fork, branch, or PR the victim merges), and (2) successfully steering the LLM to swap/misalign hunk order while keeping the hunk count correct for that file — a prompt-injection-style manipulation via file content, commit messages, or PR title/body that get included verbatim in the model context. This is plausible but non-trivial (depends on model behavior, which is not deterministically controlled by the attacker), so likelihood is moderate rather than trivially reproducible on demand. It is nonetheless a genuine, code-level gap: no defense-in-depth mechanism (content fingerprinting, per-hunk IDs, or verifying `resolvedContent` doesn't reintroduce/omits markers relative to the *specific* original ours/theirs text) exists to catch a positional mismatch even if the model misbehaves for benign reasons (e.g. truncation, reordering due to context window effects).

### Recommendation
Bind each `IHunkResolution` to an explicit hunk identifier (e.g., an index or content hash of `oursContent`/`theirsContent`) generated during `extractConflictHunks()` and require the model to echo it back, or independently verify after receipt that `hunkResolutions[i]` textually corresponds to the same-index hunk's `oursContent`/`theirsContent`/`contextBefore` fingerprint before splicing — analogous to the audit's fix of deleting/rewriting `templateIdToIndex` explicitly rather than trusting a decremented index. At minimum, log/flag files with more than one hunk for extra scrutiny or per-hunk confirmation in the UI, since single-hunk files cannot suffer this specific reordering failure mode.

### Proof of Concept
Conceptual (cannot be executed against the live LLM here, since the vulnerability depends on the model's response, not deterministic code):
1. Craft a branch/PR with a file containing two conflict hunks, e.g. hunk A (security-relevant, e.g. an auth check) and hunk B (cosmetic).
2. Include commit message / PR body text engineered to bias the model into returning `resolutions[0].hunks` in order `[B-resolution, A-resolution]` while still returning exactly 2 hunks (passes `validateResolutionPaths` count check at `app/src/lib/copilot-conflict-resolution.ts:514-519`).
3. `reassembleResolvedFile` (lines 580-591) splices `hunkResolutions[0]` (intended for B) into the position of hunk A and vice versa, with no content-identity check.
4. The user reviews a diff that looks plausible (both snippets are real code from the two branches) and commits, silently landing the wrong resolution for the security-relevant hunk.

Note: I was not able to fully verify whether upstream prompt-construction code (`formatConflictContextForPrompt`) further constrains ordering guarantees beyond the system prompt instruction text, since that function's full body was not retrieved in this session — only test expectations referencing "Conflict 1 of 1" labeling were observed. This limits certainty about how strongly the "Conflict N of M" ordering is reinforced to the model versus being advisory text only.

### Citations

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

**File:** app/src/lib/copilot-conflict-resolution.ts (L580-591)
```typescript

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
