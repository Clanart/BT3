## Analog Bug Found

### Title
Missing patch-consistency invariant check before applying partial-selection commits — silent corruption of staged/committed content ([File: app/src/lib/patch-formatter.ts])

### Summary
The Aftermath report's root cause is that a *derived* value (the post-swap invariant) is used to drive a critical state transition (accepting the swap) without ever re-validating it against the value it's supposed to preserve (`new_invariant >= old_invariant`). The GitHub Desktop analog is structurally the same shape: `formatPatch()` derives a brand-new hunk header (`oldCount`/`newCount`) from the user's line-selection state and writes it into a synthetic unified diff, then that synthetic diff is handed straight to `git apply --cached` to mutate the index — with no check anywhere that the header numbers/patch body it just built are actually self-consistent with the original hunk, or that applying it reproduces exactly the subset of lines the user visually selected.

### Finding Description
`formatPatch` walks each `DiffLine` in a hunk and increments local `oldCount`/`newCount` counters based on `file.selection.isSelected(absoluteIndex)`, then writes a hand-rolled hunk header via `formatHunkHeader(hunk.header.oldStartLine, oldCount, hunk.header.newStartLine, newCount)`: [1](#0-0) 

The only sanity check performed before emitting the patch is `if (!patch.length) throw ...` (i.e., "did we produce anything at all"), never "does the header we just synthesized match what git expects for the body we just wrote", nor "does this patch, when applied, yield exactly the file content implied by the selection": [2](#0-1) 

That unverified patch is then piped directly into `git apply --cached --unidiff-zero --whitespace=nowarn -`, i.e. a lenient apply mode that deliberately suppresses whitespace warnings and tolerates zero-context hunks: [3](#0-2) 

The counters (`oldCount`, `newCount`) are built purely from the *in-memory* `DiffLine`/`DiffHunk` model produced earlier by `diff-parser.ts`'s `parseHunk`, which itself has fragile, minimally-validated bookkeeping around the "no newline at end of file" marker (only checked for `length < 12`, no content validation) and rolling line counters that are trusted without any final cross-check against the header they were derived from: [4](#0-3) 

Because a hostile repository can be crafted with unusual but valid Git content (files without trailing newlines, mixed content near hunk boundaries, deliberately awkward line groupings designed to land exactly on the "no newline" marker edge case), an attacker who controls the cloned/fetched repository can shape the diff Desktop parses so that `formatPatch`'s recount and the real hunk boundaries diverge. Since `--whitespace=nowarn` and `--unidiff-zero` make `git apply` more permissive about exactly this kind of header mismatch, the apply can succeed silently rather than failing loudly, staging/committing content that differs from what was rendered as "selected" in the diff viewer.

### Impact Explanation
If exploited, this results in **silent corruption of what the user commits**: the user reviews and selects specific lines in the diff view, believes only those lines will be committed, but the actual staged/committed blob contains different content (extra attacker lines retained, or legitimate lines dropped) because the header written by `formatPatch` didn't match the true line accounting. This is a supply-chain-adjacent risk — a malicious repository shapes what unsuspecting contributors end up committing/pushing without their knowledge, potentially reintroducing removed secrets, backdoored lines, or masking review evasion.

### Likelihood Explanation
This requires no admin rights, no local access beyond normal use, and no social engineering beyond "clone/open a crafted repository and stage part of a file via the normal partial-commit UI" — a completely ordinary Desktop workflow. The likelihood of an exact reliable trigger is moderate-to-low without dynamic testing of `git apply`'s exact tolerance boundaries under `--unidiff-zero --whitespace=nowarn`, since I cannot execute git here to confirm precisely which malformed header/body combinations it silently accepts versus rejects with an error. The code path itself, however, has no defensive invariant check comparable to the Aftermath fix's `assert!(invariant_after >= invariant, ...)`.

### Recommendation
After building `hunkBuf`, `oldCount`, and `newCount` in `formatPatch`/`formatPatchToDiscardChanges`, add an explicit invariant check before returning the patch: verify `oldCount`/`newCount` match the number of lines actually written for each type, and ideally verify (e.g., via a dry-run `git apply --check`) that the synthesized patch cleanly and unambiguously applies to the current file content before it is passed to the real `git apply --cached` call in `applyPatchToIndex`. Fail loudly (throw) rather than letting `--whitespace=nowarn`/`--unidiff-zero` silently paper over a mismatch.

### Proof of Concept
Not independently reproducible without running `git apply` locally to confirm the exact malformed-header tolerance; the PoC would follow the same shape as the Aftermath report — construct a fixture file with a crafted "no newline at end of file" boundary and a partial line selection that causes `oldCount`/`newCount` in `app/src/lib/patch-formatter.ts:135-220` to diverge from the true hunk boundary, then assert (as in `app/test/unit/patch-formatter-test.ts`) that the resulting patch, when applied, does not equal the expected selection-only diff.

### Citations

**File:** app/src/lib/patch-formatter.ts (L135-220)
```typescript
  diff.hunks.forEach((hunk, hunkIndex) => {
    let hunkBuf = ''

    let oldCount = 0
    let newCount = 0

    let anyAdditionsOrDeletions = false

    hunk.lines.forEach((line, lineIndex) => {
      const absoluteIndex = hunk.unifiedDiffStart + lineIndex

      // We write our own hunk headers
      if (line.type === DiffLineType.Hunk) {
        return
      }

      // Context lines can always be let through, they will
      // never appear for new files.
      if (line.type === DiffLineType.Context) {
        hunkBuf += `${line.text}\n`
        oldCount++
        newCount++
      } else if (file.selection.isSelected(absoluteIndex)) {
        // A line selected for inclusion.

        // Use the line as-is
        hunkBuf += `${line.text}\n`

        if (line.type === DiffLineType.Add) {
          newCount++
        }
        if (line.type === DiffLineType.Delete) {
          oldCount++
        }

        anyAdditionsOrDeletions = true
      } else {
        // Unselected lines in new files needs to be ignored. A new file by
        // definition only consists of additions and therefore so will the
        // partial patch. If the user has elected not to commit a particular
        // addition we need to generate a patch that pretends that the line
        // never existed.
        if (
          file.status.kind === AppFileStatusKind.New ||
          file.status.kind === AppFileStatusKind.Untracked
        ) {
          return
        }

        // An unselected added line has no impact on this patch, pretend
        // it was never added to the old file by dropping it.
        if (line.type === DiffLineType.Add) {
          return
        }

        // An unselected deleted line has never happened as far as this patch
        // is concerned which means that we should treat it as if it's still
        // in the old file so we'll convert it to a context line.
        if (line.type === DiffLineType.Delete) {
          hunkBuf += ` ${line.text.substring(1)}\n`
          oldCount++
          newCount++
        } else {
          // Guarantee that we've covered all the line types
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }

      if (line.noTrailingNewLine) {
        hunkBuf += '\\ No newline at end of file\n'
      }
    })

    // Skip writing this hunk if all there is is context lines.
    if (!anyAdditionsOrDeletions) {
      return
    }

    patch += formatHunkHeader(
      hunk.header.oldStartLine,
      oldCount,
      hunk.header.newStartLine,
      newCount
    )
    patch += hunkBuf
  })
```

**File:** app/src/lib/patch-formatter.ts (L222-231)
```typescript
  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
  }

  patch = formatPatchHeaderForFile(file) + patch

  return patch
```

**File:** app/src/lib/git/apply.ts (L52-81)
```typescript
  const applyArgs: string[] = [
    'apply',
    '--cached',
    '--unidiff-zero',
    '--whitespace=nowarn',
    '-',
  ]

  const diff = await getWorkingDirectoryDiff(repository, file)

  if (diff.kind !== DiffType.Text && diff.kind !== DiffType.LargeText) {
    const { kind } = diff
    switch (diff.kind) {
      case DiffType.Binary:
      case DiffType.Submodule:
      case DiffType.Image:
        throw new Error(
          `Can't create partial commit in binary file: ${file.path}`
        )
      case DiffType.Unrenderable:
        throw new Error(
          `File diff is too large to generate a partial commit: ${file.path}`
        )
      default:
        assertNever(diff, `Unknown diff kind: ${kind}`)
    }
  }

  const patch = await formatPatch(file, diff)
  await git(applyArgs, repository.path, 'applyPatchToIndex', { stdin: patch })
```

**File:** app/src/lib/diff-parser.ts (L319-338)
```typescript
      // A marker indicating that the last line in the original or the new file
      // is missing a trailing newline. In other words, the presence of this marker
      // means that the new and/or original file lacks a trailing newline.
      //
      // When we find it we have to look up the previous line and set the
      // noTrailingNewLine flag
      if (c === DiffPrefixNoNewline) {
        // See https://github.com/git/git/blob/21f862b498925194f8f1ebe8203b7a7df756555b/apply.c#L1725-L1732
        if (line.length < 12) {
          throw new Error(
            `Expected "no newline at end of file" marker to be at least 12 bytes long`
          )
        }

        const previousLineIndex = lines.length - 1
        const previousLine = lines[previousLineIndex]
        lines[previousLineIndex] = previousLine.withNoTrailingNewLine(true)

        continue
      }
```
