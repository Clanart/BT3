### Title
Copilot conflict-resolution hunks are spliced by ordinal position with no content-correspondence check, allowing silent corruption of merged files - (File: `app/src/lib/copilot-conflict-resolution.ts`)

### Summary
`AssetTotsDaiLeverageExecutor` decoded the same `bytes` payload twice with `abi.decode`, so the second decode read fields at the wrong offsets and silently produced misaligned data. The GitHub Desktop analog is `reassembleResolvedFile` in `copilot-conflict-resolution.ts`, which reassembles a merge-conflict file by matching the model's returned `hunks` array to the conflict-marker blocks found on disk purely by **array index/order**, never by verifying that hunk `i`'s resolved content actually corresponds to conflict block `i`. Validation (`validateResolutionPaths`) only checks that the **count** of hunks matches, not that each hunk maps to the correct conflict. If the LLM (whose prompt is built from attacker-influenceable repository content — file diffs, commit messages, PR titles) returns the right number of hunks but in the wrong order, the wrong resolved content is silently spliced into each conflict block.

### Finding Description
`reassembleResolvedFile` walks the raw on-disk file line by line, and each time it encounters a well-formed `<<<<<<< ... ======= ... >>>>>>>` block it pulls the next entry from `hunkResolutions` by incrementing a plain counter: [1](#0-0) 

There is no check that `hunkResolutions[hunkIndex]` was actually generated for *that* conflict (e.g., no hash/anchor of the original `oursContent`/`theirsContent` is round-tripped and compared). The only guard, `validateResolutionPaths`, verifies solely that the *number* of hunks returned for a file equals the expected count: [2](#0-1) 

It does not verify hunk-to-hunk correspondence, order, or content plausibility (only a marker-presence check via `/^<{7}\s/m` / `/^={7}$/m` rejects blocks that still literally contain conflict markers, at parse time, in `parseCopilotConflictResolution`): [3](#0-2) 

Once `reassembleResolutions` produces `resolvedContent`, it is written straight to disk and staged without further diffing against the original conflict content: [4](#0-3) 

The prompt fed to the model is built from repository-controlled data (conflicting hunk text, surrounding context, and "recent commit messages and/or PR title/description for intent" as stated in the system prompt) — all of which an attacker can control by crafting a malicious branch/PR that a victim merges or rebases against: [5](#0-4) 

Because count-only validation passes as long as the total number of hunks per file is correct, a response where hunks for a multi-conflict file are permuted (e.g., hunk 2's content assigned to conflict 1 and vice versa) is accepted and silently spliced into the wrong location — this is structurally the same "decoded-but-misaligned" class of bug as the ABI double-decode report: a positional/ordinal correspondence assumption that isn't independently verified.

### Impact Explanation
If exploited (via prompt-injected commit messages/PR text/file content in an attacker-controlled branch that gets merged), the victim's working tree can be silently corrupted — code from one conflict resolution ends up applied to a different, unrelated conflict block in the same file — and this corrupted content is then `git add`-ed and can be committed/pushed without the user noticing, since no diff-against-original correctness check exists. This matches the "silent corruption of what the user commits or pushes" impact category.

### Likelihood Explanation
Likelihood is moderate-to-low: it requires (a) the victim to use the Copilot conflict-resolution feature on a merge with multiple conflict hunks in the same file, and (b) a model response that is order-scrambled per-file while preserving the correct total hunk count — this is more likely to occur as an LLM output-format failure than as a deliberate attacker primitive, since the attacker does not have a direct mechanism proven here to force a specific permutation of hunks (only to influence prompt content that could increase confusion/hallucination). No direct proof-of-concept forcing a scrambled-but-count-correct response was found in the codebase; this is inferred from the absence of an order/content-correspondence check rather than a demonstrated exploit chain.

### Recommendation
Have the model echo back an explicit anchor (e.g., a conflict index or a hash of `oursContent`/`theirsContent`) with each hunk resolution, and have `reassembleResolvedFile`/`validateResolutionPaths` verify that anchor matches the corresponding on-disk conflict block before splicing, rather than relying solely on array order and count.

### Proof of Concept
Not independently reproduced end-to-end against the live Copilot SDK (would require crafting a model response and injecting it into `reassembleResolutions`), but the vulnerable code path is directly demonstrable with unit-level inputs:
```ts
// original file has two independent conflicts (A then B)
const raw = [
  '<<<<<<< HEAD', 'A-ours', '=======', 'A-theirs', '>>>>>>> feature',
  'mid',
  '<<<<<<< HEAD', 'B-ours', '=======', 'B-theirs', '>>>>>>> feature',
].join('\n')

// hunk count matches (2), so validateResolutionPaths passes,
// but the model swapped which resolution belongs to which conflict
const swapped = [
  { resolvedContent: 'resolved-for-B' }, // actually intended for conflict B
  { resolvedContent: 'resolved-for-A' }, // actually intended for conflict A
]

reassembleResolvedFile(raw, swapped)
// => conflict A silently gets B's resolution and vice versa,
//    with no error raised anywhere in the pipeline
```
This mirrors the referenced call sites: [6](#0-5)  for the splicing logic and [7](#0-6)  for how it's wired into the resolution pipeline that ultimately writes to disk.

### Citations

**File:** app/src/lib/copilot-conflict-resolution.ts (L195-201)
```typescript
You will receive:
- Labels for both sides (branch names or commit refs)
- Conflict markers from each file (ours, theirs, optionally base)
- Context lines surrounding each conflict
- Delete-vs-modify conflicts where one side deleted a file and the other modified it
- When available: recent commit messages and/or PR title/description for intent

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

**File:** app/src/lib/stores/app-store.ts (L7258-7259)
```typescript
      await writeFile(absolutePath, resolution.resolvedContent, 'utf8')
      pathsToStage.push(resolution.path)
```
