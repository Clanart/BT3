### Title
Silent misalignment of AI-generated conflict resolutions to the wrong marker block due to positional (order-only) matching, with no identity binding between a hunk and its resolution — ([File: app/src/lib/copilot-conflict-resolution.ts])

### Summary
The external report's broken invariant is: a numeric value is applied to on-chain state without carrying identifying context (the token mint) to disambiguate its scale, so `set_stake` blindly trusts positional/ordinal input. The structural analog in GitHub Desktop's Copilot conflict-resolution feature is `reassembleResolvedFile`, which splices model-provided `hunkResolutions` into a file **purely by ordinal index**, matched against conflict-marker blocks found by an independent, second regex-based scan of the raw file — with no identity (hash/anchor/line-range) tying a given resolution to the specific marker block it was generated for.

### Finding Description
`extractConflictHunks` in `app/src/lib/copilot-conflict-context.ts` (lines 179-279) walks the conflicted file and builds an ordered list of `IConflictHunk` objects that are sent to the Copilot model as "Conflict 1 of N", "Conflict 2 of N", etc. [1](#0-0) 

The model returns `hunks: IHunkResolution[]`, an array of `resolvedContent` strings with **no identifying key** — just positional order, as documented in the system prompt: *"matching the 'Conflict 1 of N' ... order from the input."* [2](#0-1) 

Validation (`validateResolutionPaths`) only checks that the *count* of hunks per file matches the count originally extracted — it never re-derives or re-checks that count against the file's on-disk content at reassembly time, nor does it validate content/identity per hunk. [3](#0-2) 

Reassembly itself (`reassembleResolvedFile`) re-scans `rawContent` with its own marker regexes and splices `hunkResolutions[hunkIndex]` into the `hunkIndex`-th marker block it finds — purely by array position: [4](#0-3) 

This is the same class of bug as the report: `set_stake` used a bare `u128` amount with no mint identity, so two different mints (different decimals/scale) collapse to the same interpreted value. Here, a `resolvedContent` string has no identity binding to "which marker block" it belongs to; it is applied solely by array index, and that index is derived from **two independently-run marker scans** (one in `extractConflictHunks`, one in `reassembleResolvedFile`) that are not guaranteed to observe the same file bytes or produce the same hunk count/order.

The two scans can diverge because:
- `rawContent` is captured once during context-building (`buildConflictContext`) and is a snapshot of the file at that time; nothing prevents the working-tree file from changing before the model's response is applied (e.g. another git operation, a build tool, an editor autosave, or content fetched/rewritten mid-merge from a git hook triggered by the untrusted merge).
- `getHunkSkipReason` can cause a file to be size-skipped (`hunks: []`, `skippedReason` set) at context-build time, yet the file's `rawContent` is still stored and still contains real marker blocks; if this skip logic and downstream handling diverge for edge sizes, the model never sees the true hunk count while the reassembler still scans all markers.
- An attacker who controls the **remote/incoming branch content** (a classic Desktop attack surface — a malicious `theirs` side merged via `git pull`/`git fetch` + merge) can craft conflicted file content containing extra malformed-but-recoverable marker sequences, or content whose `oursContent`/`theirsContent` includes strings that satisfy `extractConflictHunks`'s stricter multi-stage state machine differently than `reassembleResolvedFile`'s simpler lookahead-based scan (e.g., interior `<<<<<<<`/`=======`/`>>>>>>>` sequences embedded in one side's text without diff3 `|||||||` markers), causing the two functions to count/order conflict blocks differently for the *same* file text.

When the counts/order diverge, `hunkIndex < hunkResolutions.length` silently under/over-applies resolutions: some marker blocks get the wrong resolution content spliced in, some resolved content is dropped, and the final `.get()` writes this as the fully "resolved," marker-free file that the app then stages and lets the user commit — with no error surfaced (validation passed on the file-level hunk *count*, not the applied content).

### Impact Explanation
This corrupts the exact value the report targets by analogy: not a stake amount, but **what the user actually commits/pushes**. Because the reassembled file is by design free of conflict markers, the user has no visual signal that a wrong resolution (from a *different* conflict block, or content shifted by one) was silently spliced into their code — potentially reintroducing a bug that was supposed to be fixed on one side, dropping a security check, or committing attacker-influenced code from `theirsContent` in a place the user never approved. Since the attacker's own branch/PR supplies the `theirs` content that seeds the mismatch, this is squarely "unprivileged... attacker controls a... fetched repository... result is... silent corruption of what the user commits or pushes."

### Likelihood Explanation
Reaching this path requires: (1) the Copilot conflict-resolution feature is used (opt-in, but a normal in-product flow — `docs/technical/copilot-conflict-resolution` region of code, `copilot-store.ts`), and (2) a conflicted file whose text causes the two independent marker-scanning implementations (`extractConflictHunks` vs. `reassembleResolvedFile`'s marker regexes) to disagree on hunk count/order, which an attacker fully controls by shaping the content of the branch/PR they get merged against. No local access, no admin rights, and no unnatural steps beyond a normal merge/rebase with conflicts are needed — likelihood is moderate, gated mainly by finding an exact malformed-marker payload that the two scanners parse differently (I could not fully verify a concrete diverging input from the index due to truncated file content beyond what was fetched; this is the main residual uncertainty).

### Recommendation
Bind each `IHunkResolution` to an explicit identity derived at hunk-extraction time (e.g., a stable hash of the exact marker block's start/end line offsets or the extracted `oursContent`/`theirsContent`/`baseContent`), and require `reassembleResolvedFile` to verify that the marker block it is about to replace exactly matches the one the model was shown before splicing, refusing (and falling back to manual resolution) instead of guessing by ordinal index if a mismatch is found. Additionally, re-run `extractConflictHunks` on the same `rawContent` string that is later passed to `reassembleResolvedFile` (never a possibly-stale snapshot) and assert count/order equality with an exception, not a silent `hunkIndex < hunkResolutions.length` guard.

### Proof of Concept
Conceptual PoC (exact diverging payload not fully confirmed from available code):
1. Attacker opens a PR/branch (`theirs`) whose file content, when merged, produces a conflicted file where one side's `theirsContent`/`oursContent` contains a line matching `reassemblyOursMarker`/`reassemblySeparatorMarker` patterns but is nested inside content that `extractConflictHunks`'s stateful line walker treats as belonging to a single hunk (e.g., because it appears inside what its parser treats as `theirsLines` before finding `hunkEnd`), while `reassembleResolvedFile`'s independent lookahead sees it as a second, separate marker block.
2. User runs a merge/rebase against this branch in Desktop and invokes "Resolve with Copilot".
3. `extractConflictHunks` reports N hunks to the model; the model returns N `resolvedContent` entries in order.
4. `reassembleResolvedFile` — scanning the same text independently — detects N±1 blocks or a different block order, so `hunkResolutions[hunkIndex]` is spliced into the wrong marker block.
5. The reassembled, marker-free file is shown as fully resolved and written to disk/staged without any warning, even though content from one conflict was applied to a different location than intended. [5](#0-4) [6](#0-5)

### Citations

**File:** app/src/lib/copilot-conflict-context.ts (L179-279)
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

    // The ours marker line is at oursStart - 1
    const markerStart = oursStart - 1
    const contextStart = Math.max(0, markerStart - contextLines)
    const contextEnd = Math.min(lines.length - 1, hunkEnd + contextLines)

    // Clamp context to not include conflict markers from adjacent hunks
    const contextBeforeLines: Array<string> = []
    for (let j = markerStart - 1; j >= contextStart; j--) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextBeforeLines.unshift(lines[j])
    }

    const contextAfterLines: Array<string> = []
    for (let j = hunkEnd + 1; j <= contextEnd; j++) {
      if (isConflictMarker(lines[j])) {
        break
      }
      contextAfterLines.push(lines[j])
    }

    const contextBefore = contextBeforeLines.join('\n')
    const contextAfter = contextAfterLines.join('\n')

    hunks.push({
      oursContent: oursLines.join('\n'),
      theirsContent: theirsLines.join('\n'),
      baseContent: hasBase ? baseLines.join('\n') : null,
      contextBefore,
      contextAfter,
    })
  }

  return hunks
}
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L243-245)
```typescript
Field rules:

hunks: An ordered array with one entry per conflict in the file, matching the "Conflict 1 of N", "Conflict 2 of N" order from the input. Each entry's resolvedContent is ONLY the merged content that replaces that specific conflict marker block (the region between <<<<<<< and >>>>>>>). Do NOT include surrounding non-conflicted code — the application splices each resolution into the original file automatically. If the resolution is to accept one side entirely, return that side's content verbatim. For an intentional deletion, use an empty string. For delete-vs-modify conflicts, hunks must be an empty array.
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
