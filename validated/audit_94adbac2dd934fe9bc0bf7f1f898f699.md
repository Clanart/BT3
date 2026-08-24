## Analog Found: UTF-8-only diff decoding corrupts partially-staged commits when a file contains non-UTF-8 bytes

### Title
Hardcoded UTF-8 Decoding of Raw Diff Buffers Silently Corrupts Partially Staged/Committed File Content - (`app/src/lib/git/diff.ts`)

### Summary
The Halborn report's root cause is a broken protocol invariant: a value is treated as if it always has a fixed, assumed unit (18 decimals) without checking the real unit, so a downstream arithmetic step silently produces the wrong result. GitHub Desktop has the same shape of bug in its diff pipeline: `diffFromRawDiffOutput` assumes every raw diff buffer coming from `git` is UTF-8-encoded, even though the buffer's actual bytes are governed entirely by the *content of files in the repository*, which is fully attacker-controlled (any file a user clones, fetches, or checks out from a malicious remote/PR). When the assumption is wrong, the decoded diff text used to reconstruct a `git apply` patch no longer matches the real working-directory bytes, and that corrupted text is what gets written into the index/commit for partially-staged changes.

### Finding Description
`diffFromRawDiffOutput` explicitly hardcodes the encoding: [1](#0-0) 

```
function diffFromRawDiffOutput(output: Buffer): IRawDiff {
  // for now we just assume the diff is UTF-8, but given we have the raw buffer
  // we can try and convert this into other encodings in the future
  const result = output.toString('utf-8')
  ...
}
```

`Buffer.toString('utf-8')` is lossy for any byte sequence that isn't valid UTF-8: invalid sequences are silently replaced with the U+FFFD replacement character. Files with legacy/non-UTF-8 encodings (Latin-1, Shift-JIS, GBK, or simply files containing arbitrary raw/binary-adjacent byte sequences that git still treats as text) are completely valid, ordinary git blobs — nothing prevents a repository the user clones/fetches from containing such content.

This decoded (and now potentially lossy) string is parsed into `DiffLine` objects by `DiffParser.parse` (`app/src/lib/diff-parser.ts`), and when the user performs a **partial commit** (selecting only some lines of a hunk), Desktop reconstructs a synthetic unified-diff patch purely from these `DiffLine.text` strings: [2](#0-1) 

That patch is then piped to `git apply --cached` to build the index entry that becomes the commit: [3](#0-2) 

Because `formatPatch` includes not just the user-selected add/delete lines but also the *context lines* of the same hunk (unmodified lines needed for the patch to apply), any U+FFFD corruption introduced during the UTF-8 decode step propagates into content that the user never intended to change and is not shown as a "change" in the diff review UI (context lines render the same whether or not they contain replacement characters, and there is no size/byte-identity check before `git apply` runs).

### Impact Explanation
This breaks the same invariant class as the LP-decimals bug: a value's "unit" (byte encoding) is assumed rather than verified, and the mismatch flows straight into a downstream calculation/reconstruction that materially changes the committed artifact. Here the corrupted artifact is the actual git object written to the repository — unmodified lines adjacent to a partial selection can be silently rewritten with `U+FFFD` (`EF BF BD`) bytes instead of the file's original bytes, producing a commit whose content differs from what the user believes they reviewed and approved. This is a direct instance of "silent corruption of what the user commits," matching the specified valid-impact category, and the attacker's leverage is simply committing/hosting a file with bytes that are not valid UTF-8 in a repository the victim clones or fetches — no local access, malware, or social engineering is required beyond normal repository interaction.

### Likelihood Explanation
Any repository containing files in a legacy 8-bit encoding, or containing text with even a single malformed/foreign byte sequence, will trigger this path whenever a Desktop user opens a partial-selection commit UI (a routine, encouraged workflow — selecting individual lines/hunks to stage). The comment in the source (`"for now we just assume the diff is UTF-8"`) confirms this is a known, unaddressed gap rather than a defended edge case; there is no `isValidBuffer`/size-style guard analogous to `isValidBuffer`/`isDiffTooLarge` that checks whether the decode round-trips losslessly before the text is reused to build a patch for `git apply --cached`.

### Recommendation
Before using the decoded string to reconstruct a patch for `git apply`, verify the encode/decode round-trip is lossless (e.g., re-encode the decoded string back to a buffer and compare to the original bytes), and if it isn't, fall back to a full-file `git add`/whole-hunk staging path (bypassing text reconstruction) instead of silently emitting `U+FFFD` into the patch that gets applied to the index. Longer term, honor `.gitattributes`/`core.attributesFile` `working-tree-encoding` the same way git itself does before decoding, rather than hardcoding UTF-8.

### Proof of Concept
1. Create a file whose bytes are not valid UTF-8 (e.g., a Latin-1 file containing byte `0xE9` for `é`), commit it.
2. Edit an unrelated line elsewhere in the same hunk and open Desktop's changes view; only stage/commit that one line via partial line selection (not "select all").
3. `getWorkingDirectoryDiff` → `diffFromRawDiffOutput` decodes the raw diff buffer with `Buffer.toString('utf-8')`; the invalid `0xE9` byte is replaced with U+FFFD in the parsed `DiffLine.text` for the context line containing it.
4. `formatPatch` reconstructs the patch from these `DiffLine.text` values (including the corrupted context line) and `applyPatchToIndex` pipes it to `git apply --cached`.
5. Inspect the resulting index/commit blob for that file: the previously-untouched line now contains `EF BF BD` (UTF-8 encoding of U+FFFD) instead of the original `0xE9` byte — the committed file content differs from the working directory content the user intended to preserve, with no warning shown in the UI.

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

**File:** app/src/lib/patch-formatter.ts (L113-133)
```typescript
/**
 * Creates a GNU unified diff based on the original diff and a number
 * of selected or unselected lines (from file.selection). The patch is
 * formatted with the intention of being used for applying against an index
 * with git apply.
 *
 * Note that the file must have at least one selected addition or deletion,
 * ie it's not supported to use this method as a general purpose diff
 * formatter.
 *
 * @param file  The file that the resulting patch will be applied to.
 *              This is used to determine the from and to paths for the
 *              patch header as well as retrieving the line selection state
 *
 * @param diff  The source diff
 */
export function formatPatch(
  file: WorkingDirectoryFileChange,
  diff: ITextDiff | ILargeTextDiff
): string {
  let patch = ''
```

**File:** app/src/lib/git/apply.ts (L52-83)
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
```
