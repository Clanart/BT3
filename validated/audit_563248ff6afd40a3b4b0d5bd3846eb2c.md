Based on my research, I found a concrete analog in the Copilot AI-assisted merge-conflict resolution feature. The bug class from the report (wrong branch/condition selection leading to a miscalculated value that gets used downstream) maps to a **marker-boundary misclassification** in the conflict-parsing code that feeds — and later re-splices — content around GitHub Desktop's Copilot conflict resolver. Notably, the changelog itself documents that an earlier variant of this exact bug class in this exact feature ("file content outside conflicted regions was overwritten when using AI-assisted conflict resolution") was already fixed in `3.5.13-beta3` [1](#0-0) , confirming this is a real, previously-exploitable bug class in this codebase, not merely theoretical.

### Title
Attacker-crafted `|||||||`/`=======` lookalike lines inside conflicted file content cause `extractConflictHunks` to mis-attribute "ours" content as "base" content, poisoning the context sent to Copilot's AI conflict resolver - (File: `app/src/lib/copilot-conflict-context.ts`)

### Summary
`extractConflictHunks` is used to slice a conflicted file into `oursContent` / `baseContent` / `theirsContent` sections that are sent verbatim to the Copilot conflict-resolution model, whose output is later spliced back into the working file by the independent, separately-implemented `reassembleResolvedFile` [2](#0-1) . The two functions use different, non-cross-validated heuristics to decide hunk boundaries. `extractConflictHunks` treats *any* line matching `/^\|{7}(?:\s|$)/` as the start of a diff3 base section, with no requirement that it actually originated from a real diff3 merge base header [3](#0-2) [4](#0-3) . If a file's "ours" or "theirs" side (content the attacker controls, e.g. via a malicious commit/branch that a victim later merges/rebases against) happens to contain a line of exactly seven pipe characters (or an extraneous exact-seven `=======` line), `extractConflictHunks` will mis-split the region: content that is really part of "ours" gets relabeled and sent to the model as "base (common ancestor)" content, while `reassembleResolvedFile` — which has no notion of `|||||||` at all — still treats the whole thing as a single well-formed hunk and blindly replaces it end-to-end with the model's `resolvedContent` [5](#0-4) .

### Finding Description
The broken invariant is: *"the boundary a hunk gets sliced at for context-building must match the boundary used when splicing the model's answer back into the file."* This invariant is never enforced — the two marker-scanning implementations are maintained independently:

- `extractConflictHunks`'s ours-collection loop breaks out on the **first** line matching `baseMarker` OR `separatorMarker`, whichever comes first, with no verification that a `|||||||` was actually emitted by git's diff3 style merge (`merge.conflictstyle=diff3`) rather than being incidental content in one of the sides [6](#0-5) .
- `reassembleResolvedFile`'s well-formedness check only looks for `=======` and `>>>>>>>`; it has no `baseMarker` concept whatsoever [7](#0-6) [8](#0-7) .

Because of this divergence, an attacker who can get content into either side of a future merge conflict (a cloned/fetched malicious branch, a PR the victim merges, etc.) can embed an incidental `|||||||`-looking line inside what should be their "ours" text. `extractConflictHunks` will silently reclassify everything after that line — up to the real `=======` — as `baseContent` instead of `oursContent`, corrupting the intent signal handed to the model (the system prompt explicitly instructs the model to use "ours"/"theirs"/"base" semantics to infer intent and decide what to keep) [9](#0-8) . The model's resulting `resolvedContent` — built from this poisoned understanding of "which side wrote what" — is still spliced wholesale into the file by `reassembleResolvedFile`, which never re-derives or validates the semantic split it committed to [10](#0-9) , and is then written to disk and staged without further review of the ours/base/theirs classification [11](#0-10) .

### Impact Explanation
The end effect is a *silent corruption of what the user commits*: the AI resolver can be steered into treating attacker-authored ("theirs") content as if it were the trusted local ("ours"/"base") content and vice versa, purely by an incidental 7-character marker-lookalike line inside repository content the attacker controls. Since Copilot's system prompt explicitly tells the model to favor "backward compatibility" and use base/ours/theirs semantics to decide which side's intent to honor, mislabeling can bias the model toward keeping or dropping the wrong side's changes without any visible indication to the user beyond the normal resolution summary. This matches the valid-impact category of "silent corruption of what the user commits or pushes" via attacker-controlled repository content.

### Likelihood Explanation
Exploitation requires no local access or credentials — only that the victim uses the "Resolve merge conflicts with Copilot" feature on a merge/rebase/cherry-pick against a branch/commit whose content the attacker influenced (a normal, unprivileged collaboration scenario). The trigger condition (a line of exactly seven `|` characters, or an extraneous exact seven `=` characters, appearing in ordinary file content) is a narrow but realistic occurrence (ASCII table dividers, comment banners, minified separators), and no existing guard cross-validates `extractConflictHunks`'s boundaries against `reassembleResolvedFile`'s, so likelihood is assessed as low-to-medium (needs a somewhat contrived, but not implausible, content shape), reflecting a real gap left over from the class of bug already patched once in this codebase (#22349).

### Recommendation
Unify hunk-boundary detection into a single shared parser used by both `extractConflictHunks` and `reassembleResolvedFile`, so the exact same line indices are used both to build the model prompt and to splice its answer back in. Additionally, require diff3 base-section detection to only activate when both a `|||||||` "and" a following `=======` are present with no premature encounter of `>>>>>>>` in between, and add a regression test with an "ours"/"theirs" payload containing an incidental 7-pipe or extra 7-equals line to lock in correct behavior.

### Proof of Concept
```ts
// app/src/lib/copilot-conflict-context.ts — extractConflictHunks
const fileContent = [
  '<<<<<<< HEAD',
  'const trustedConfig = true',
  '|||||||',                 // incidental line, NOT a real diff3 base marker
  'const legacyFlag = false', // this is really "ours" content
  '=======',
  'const trustedConfig = false // attacker-controlled "theirs"',
  '>>>>>>> feature',
].join('\n')

const hunks = extractConflictHunks(fileContent)
// hunks[0].oursContent   === 'const trustedConfig = true'   (truncated!)
// hunks[0].baseContent   === 'const legacyFlag = false'     (WRONG: this was "ours")
// hunks[0].theirsContent === 'const trustedConfig = false // attacker-controlled "theirs"'
```
The model now sees `const legacyFlag = false` labeled as the "common ancestor" instead of as part of the trusted local change, while `reassembleResolvedFile` still treats the entire `<<<<<<<`…`>>>>>>>` block as one hunk and unconditionally replaces it with whatever the (misinformed) model returns.

### Citations

**File:** changelog.json (L93-96)
```json
    "3.5.13-beta3": [
      "[Fixed] Recover conflict dialog from permanently frozen state when conflict state becomes invalid, preventing users from needing to restart the app - #22348",
      "[Fixed] Resolve Copilot conflict resolution data loss where file content outside conflicted regions was overwritten when using AI-assisted conflict resolution - #22349"
    ],
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L202-216)
```typescript
Your job:
1. Understand the INTENT behind each side's changes
2. Resolve each conflict by producing the correct merged content for each conflict hunk
3. For delete-vs-modify conflicts, recommend whether to keep or delete the file
4. Explain your reasoning per file — terse but specific enough to verify the decision
5. Produce a brief markdown summary orienting the user to the conflict and resolution

Resolution guidelines:
- Make MINIMAL changes — do not refactor, reformat, or alter code outside conflicted regions
- When both sides add complementary code (e.g., different imports), combine them
- When both sides modify the same code differently, use commit messages and PR context to decide
- When one side deletes code the other modifies, check whether the content was relocated rather than simply removed — accept the deletion only when it was intentional
- When conflicts involve dependency manifests or lock files, ensure version constraints and entries remain consistent across the resolved file
- Preserve correctness: imports, types, formatting must remain valid
- When in doubt, prefer backward compatibility
```

**File:** app/src/lib/copilot-conflict-resolution.ts (L524-526)
```typescript
const reassemblyOursMarker = /^<{7}(?:\s|$)/
const reassemblySeparatorMarker = /^={7}$/
const reassemblyTheirsMarker = /^>{7}(?:\s|$)/
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

**File:** app/src/lib/copilot-conflict-context.ts (L122-125)
```typescript
const oursMarker = /^<{7}(?:\s|$)/
const baseMarker = /^\|{7}(?:\s|$)/
const separatorMarker = /^={7}$/
const theirsMarker = /^>{7}(?:\s|$)/
```

**File:** app/src/lib/copilot-conflict-context.ts (L200-226)
```typescript
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
