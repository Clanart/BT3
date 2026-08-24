### Title
Diff parser hard-codes UTF-8 decoding of raw git diff output, causing silent corruption of partial commits/discards for non-UTF-8 files - ([File: app/src/lib/git/diff.ts])

### Summary
The reported crvUSD bug reduces to a single broken invariant: the protocol treats an externally-supplied value (crvUSD) as always numerically equal to a trusted reference (USD) without verification, and derives financial calculations directly from that unverified equivalence. The Desktop analog is `diffFromRawDiffOutput` in `app/src/lib/git/diff.ts`, which unconditionally decodes the raw `git diff` byte buffer as UTF-8, explicitly acknowledging the assumption in its own comment, and then feeds the resulting (possibly mis-decoded) text into the patch-generation pipeline that is applied back to the user's index/working tree. [1](#0-0) 

### Finding Description
`buildDiff`/`getWorkingDirectoryDiff` invoke `git diff ... --patch-with-raw -z --no-color` with `encoding: 'buffer'` to get the raw byte stream, then hand it to `diffFromRawDiffOutput`, which does:

```
// for now we just assume the diff is UTF-8, but given we have the raw buffer
// we can try and convert this into other encodings in the future
const result = output.toString('utf-8')
``` [1](#0-0) 

Git itself is encoding-agnostic — it does not know or care what encoding a tracked file uses (Latin-1, Shift-JIS, UTF-16, etc.), and repository content, including files an attacker fully controls when the victim clones or checks out an attacker-authored repo/branch/PR, can legitimately be non-UTF-8. Decoding raw diff bytes as UTF-8 when the underlying file bytes are not valid UTF-8 causes the JS string produced by `Buffer.toString('utf-8')` to replace invalid byte sequences with the U+FFFD replacement character or otherwise misalign multi-byte sequences with ASCII diff markers (`@@`, `+`, `-`, tab/space prefixes). This corrupted string is then parsed by `DiffParser` into hunks/lines, ` [2](#0-1) ` and those hunks are exactly what `formatPatch` / `formatPatchToDiscardChanges` in `app/src/lib/patch-formatter.ts` use to rebuild unified-diff patches line-by-line from `line.text`, which are subsequently piped to `git apply --cached` (for staging selected lines) or `git apply` (for discarding selected lines). [3](#0-2) [4](#0-3) 

Because the patch text is built from the UTF-8-decoded (and thus potentially already-corrupted) representation rather than the original bytes, a `git apply` of this reconstructed patch can silently write different bytes than what actually existed in the working tree/index, or fail unpredictably, meaning the user's partial-stage/partial-discard operations no longer reflect what they saw or intended — a silent corruption of what the user commits.

### Impact Explanation
This falls squarely under "silent corruption of what the user commits or pushes." A user working with non-ASCII/non-UTF-8-encoded files (which is common for legacy codebases, localized resource files, or files created on non-UTF-8 locales) who uses Desktop's line/hunk-level staging or discard-selection features could have their staged content or working directory silently diverge from what the diff view displayed, without any error being surfaced to them, since `git apply` will happily apply a patch built from replacement characters as long as it's syntactically well-formed. The comment in the code itself ("we just assume the diff is UTF-8 ... for now") shows this is a known, unaddressed gap rather than a validated safe assumption.

### Likelihood Explanation
Likelihood is dependent on repository content: any repository (including attacker-influenced repositories a victim clones, or files introduced via a malicious PR/branch the victim checks out) containing files with non-UTF-8 byte sequences will trigger this path whenever the user views a diff or performs partial staging/discarding on that file. Because Desktop always decodes with `'utf-8'` unconditionally, there is no existing guard, encoding detection, or fallback that prevents the corrupted decode from being used to construct patches that are subsequently applied to the index/working directory.

### Recommendation
Detect or preserve the original byte encoding when parsing raw diff output (e.g., only decode line-marker/header bytes as ASCII while treating line content as opaque byte spans, or use encoding sniffing similar to what's already done elsewhere for file content), and when the encoding cannot be safely round-tripped, refuse partial-patch generation (fall back to whole-file staging/discard) rather than silently reconstructing a patch from lossily-decoded text.

### Proof of Concept
Not independently reproduced in this session (no execution environment available); the concrete corruption path is demonstrated by static code tracing: `getWorkingDirectoryDiff` → `buildDiff` → `diffFromRawDiffOutput` (hard-coded `toString('utf-8')`) [1](#0-0)  → `DiffSelection`-based `formatPatch`/`formatPatchToDiscardChanges` reconstructing patch text from `line.text` [5](#0-4)  → `git apply --cached`/`git apply` in `app/src/lib/git/apply.ts` [4](#0-3) . A conceptual repro: commit a file containing bytes that are invalid UTF-8 but valid in another single/multi-byte encoding, modify a line adjacent to those bytes, then use Desktop's line-level "stage selected lines" or "discard selected lines" feature and inspect the resulting file/index content versus the intended selection.

### Citations

**File:** app/src/lib/git/diff.ts (L788-796)
```typescript
function diffFromRawDiffOutput(output: Buffer): IRawDiff {
  // for now we just assume the diff is UTF-8, but given we have the raw buffer
  // we can try and convert this into other encodings in the future
  const result = output.toString('utf-8')

  const pieces = result.split('\0')
  const parser = new DiffParser()
  return parser.parse(forceUnwrap(`Invalid diff output`, pieces.at(-1)))
}
```

**File:** app/src/lib/patch-formatter.ts (L129-232)
```typescript
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''

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

  // If we get into this state we should never have been called in the first
  // place. Someone gave us a faulty diff and/or faulty selection state.
  if (!patch.length) {
    log.debug(`formatPatch: empty path for ${file.path}`)
    throw new Error(`Could not generate a patch, no changes`)
  }

  patch = formatPatchHeaderForFile(file) + patch

  return patch
}
```

**File:** app/src/lib/git/apply.ts (L52-119)
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

  return Promise.resolve()
}

/**
 * Discards the local changes for the specified file based on the passed diff
 * and a selection of lines from it.
 *
 * When passed an empty selection, this method won't do anything. When passed a
 * full selection, all changes from the file will be discarded.
 *
 * @param repository The repository in which to update the working directory
 *                   with information from the index
 *
 * @param filePath   The relative path in the working directory of the file to use
 *
 * @param diff       The diff containing the file local changes
 *
 * @param selection  The selection of changes from the diff to discard
 */
export async function discardChangesFromSelection(
  repository: Repository,
  filePath: string,
  diff: ITextDiff,
  selection: DiffSelection
) {
  const patch = formatPatchToDiscardChanges(filePath, diff, selection)

  if (patch === null) {
    // When the patch is null we don't need to apply it since it will be a noop.
    return
  }

  const args = ['apply', '--unidiff-zero', '--whitespace=nowarn', '-']

  await git(args, repository.path, 'discardChangesFromSelection', {
    stdin: patch,
  })
```
