## Title
Silent trailing-newline corruption when partially staging/discarding a hunk that ends the file without a newline — (File: `app/src/lib/patch-formatter.ts`)

## Summary
`formatPatch()` and `formatPatchToDiscardChanges()` build a synthetic unified diff from the user's line-by-line selection so partial commits/discards can be applied against the index with `git apply --cached`/`git apply`. When a hunk's final `Delete`/`Add` pair straddles the "no newline at end of file" marker and the user selects only one side of that pair, the generated patch attaches the `\ No newline at end of file` marker to a line that is **not actually the last line emitted in the hunk**, producing an internally-inconsistent patch that `git apply` is not designed to validate against, mirroring the report's core defect: one side of a paired calculation is adjusted (the kept context/add line) while the other side's flag is carried over unchanged, corrupting the invariant that the marker must apply to the true end-of-file line.

## Finding Description
`git diff` represents an EOF-without-newline change as a Delete/Add pair, each individually flagged via `DiffLine.noTrailingNewLine` [1](#0-0) . In `formatPatch`, when a `Delete` line is *not* selected it is rewritten into a context line, and separately when the same line has `noTrailingNewLine` set, a `\ No newline at end of file` marker is appended right after it, regardless of whether more content (a selected `Add` line) follows in the same hunk [2](#0-1) . Symmetrically, `formatPatchToDiscardChanges` performs the analogous unselected-`Add`→context conversion and inherited-marker append [3](#0-2) .

Both functions independently decide inclusion/exclusion per line but only track it via a single `noTrailingNewLine` boolean lifted verbatim from the original diff line, never re-evaluated against the new position of that line within the emitted patch (i.e. whether anything else follows it in the hunk). This is the same broken invariant as the funding-fee bug: two coupled sides of a change (the "no newline" delete and its paired add) are supposed to move together, but Desktop's selection logic only adjusts one side (which lines get dropped/converted) while blindly carrying the "no newline" flag through, without adjusting or dropping it when a subsequent added line is retained.

The existing unit test at `app/test/unit/patch-formatter-test.ts` for the "no newline" case only checks `patch.includes('\\ No newline at end of file')` and `patch.includes('+it could be')` — it never applies the produced patch to verify the resulting file content is correct [4](#0-3) , so this malformed-but-syntactically-plausible patch shape is not caught by any existing guard.

The patch is fed straight into `git apply --cached ... -` for staging [5](#0-4)  and into `git apply --unidiff-zero ... -` for discarding [6](#0-5) , both driven entirely by the string built in `patch-formatter.ts` with no post-hoc structural validation.

## Impact Explanation
If `git apply` accepts the malformed hunk (rather than rejecting it outright), it will misplace the newline state of the file — either merging the retained context line and the subsequently added line together without a separating newline, or dropping/duplicating the newline at EOF — resulting in **silent corruption of what the user commits or pushes** without any error surfaced to the user, exactly the "no error, wrong result" failure mode called out as high-impact in the source report. Because this path is used both for partial-stage (`applyPatchToIndex`) and partial-discard (`discardChangesFromSelection`) flows, both commit content and working-directory content are exposed.

## Likelihood Explanation
This requires no attacker-supplied repository trickery beyond a completely ordinary Git state: any file whose last line lacks a trailing newline, modified so the last line changes (a very common occurrence, e.g. editing a file without a final newline), combined with a user doing partial (line-level) staging or discarding and choosing to keep only one side (e.g., accept the new last line but leave the old deletion unstaged, or vice versa). This is a normal, unprivileged, single-user workflow — no local/physical access, no malware, no elevated privileges — which fits squarely in the "unprompted natural user steps" category rather than the excluded "unnatural user steps."

## Recommendation
Only emit the `\ No newline at end of file` marker when the line it is attached to is genuinely the last line written to the hunk buffer for that patch (i.e., re-derive the marker placement after all inclusion/exclusion decisions are made, rather than copying the flag verbatim from the original `DiffLine`). If the "true" last line's opposite counterpart is dropped/converted, either drop the marker as well or synthesize a context line that legitimately terminates the file, ensuring `formatPatch` and `formatPatchToDiscardChanges` never produce a hunk where a `\ No newline` marker precedes further emitted content.

## Proof of Concept
Not independently verified end-to-end against a live `git apply` binary in this session (no shell access), so it is unconfirmed whether `git apply` errors out or silently corrupts — this is the main uncertainty in this analog. However, the vulnerable code path can be reproduced with the existing test fixture logic:

1. Diff (as in `app/test/unit/patch-formatter-test.ts:341-372`) where the old file's last line lacks a trailing newline:
```
@@ -23,5 +24,5 @@ and more stuff
 
 
 
-
-and fun stuff? I dnno
\ No newline at end of file
+and fun stuff? I dnno
+it could be,
```
2. User selects only the last `Add` line (`it could be,`) via `DiffSelection.withLineSelection(7, true)`, leaving the `Delete` lines and the first `Add` line unselected.
3. `formatPatch` converts the unselected `Delete "and fun stuff? I dnno"` (which carries `noTrailingNewLine: true`) into a context line and appends the marker immediately after it, then still appends the selected `+it could be,` line afterward — see `app/src/lib/patch-formatter.ts:190-205`.
4. The resulting hunk places `\ No newline at end of file` before a line that is not actually last, which is exactly what the current test suite does not validate (it never applies the patch and re-reads file bytes), leaving this class of malformed hunk unguarded before being handed to `git apply` in `app/src/lib/git/apply.ts:80-81`.

Because I could not execute `git apply` against this exact crafted patch in this session, I cannot state with certainty whether it errors or silently corrupts — a Devin session with shell/filesystem access would be needed to confirm the exact `git apply` behavior and downstream file corruption.

### Citations

**File:** app/src/lib/diff-parser.ts (L319-337)
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
```

**File:** app/src/lib/patch-formatter.ts (L190-205)
```typescript
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
```

**File:** app/src/lib/patch-formatter.ts (L293-312)
```typescript
      } else {
        if (line.type === DiffLineType.Add) {
          // An unselected added line will stay in the file after discarding the changes,
          // so we just print it untouched on the diff.
          oldCount++
          newCount++
          hunkBuf += ` ${line.text.substring(1)}\n`
        } else if (line.type === DiffLineType.Delete) {
          // An unselected removed line has no impact on this patch since it's not
          // found on the current working copy of the file, so we can ignore it.
          return
        } else {
          // Guarantee that we've covered all the line types.
          assertNever(line.type, `Unsupported line type ${line.type}`)
        }
      }

      if (line.noTrailingNewLine) {
        hunkBuf += '\\ No newline at end of file\n'
      }
```

**File:** app/test/unit/patch-formatter-test.ts (L341-372)
```typescript
    it('creates the right patch when a `No newline` marker is involved', async () => {
      const rawDiff = [
        '--- a/file.md',
        '+++ b/file.md',
        '@@ -23,5 +24,5 @@ and more stuff',
        ' ',
        ' ',
        ' ',
        '-',
        '-and fun stuff? I dnno',
        '\\ No newline at end of file',
        '+and fun stuff? I dnno',
        '+it could be,',
      ].join('\n')
      const diff = await parseDiff(rawDiff)

      // Select the second added line
      const selection = DiffSelection.fromInitialSelection(
        DiffSelectionType.None
      ).withLineSelection(7, true)

      const file = new WorkingDirectoryFileChange(
        'file.md',
        { kind: AppFileStatusKind.Modified },
        selection
      )

      const patch = formatPatch(file, diff)

      assert(patch.includes('\\ No newline at end of file'))
      assert(patch.includes('+it could be'))
    })
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

**File:** app/src/lib/git/apply.ts (L86-120)
```typescript
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
}
```
