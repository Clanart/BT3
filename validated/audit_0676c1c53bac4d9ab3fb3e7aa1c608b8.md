### Title
Partial-stage/discard patches trust unvalidated hunk-header line numbers applied with `--unidiff-zero`, allowing silent corruption of staged/committed content - (File: `app/src/lib/git/apply.ts`)

### Summary
The reported Sway bug is about a broken invariant: computed numeric values (`pow` results) are used without validating they still represent what the caller believes they represent, and that value is later used to move real assets. The GitHub Desktop analog is the same class of "unvalidated numeric metadata trusted downstream": the unified-diff hunk header numbers (`oldStartLine`, `oldLineCount`, `newStartLine`, `newLineCount`) parsed by `DiffParser` are never cross-checked against the actual hunk content, yet they are echoed back verbatim into patches that are applied to the git index with `git apply --cached --unidiff-zero`, a flag that removes git's normal context-based safety net for locating where a hunk belongs.

### Finding Description
`DiffParser.numberFromGroup` extracts the four hunk-header numbers purely via regex + `parseInt`, with no check that the declared `oldLineCount`/`newLineCount` match the number of context/added/removed lines actually parsed in the hunk body: [1](#0-0) 

`formatPatch` (used for partial commits) and `formatPatchToDiscardChanges` (used for discarding a partial selection) rebuild a patch for `git apply` by writing out `hunk.header.oldStartLine` / `hunk.header.newStartLine` unchanged, only recomputing the *counts* from the lines actually selected: [2](#0-1) 

That patch is then handed to git with `--unidiff-zero`, which tells `git apply` to accept zero-context hunks and locate the change strictly by the header's start-line offset instead of by matching surrounding context lines: [3](#0-2) [4](#0-3) 

Because `--unidiff-zero` disables git's normal fuzzy context verification, the *only* thing that decides where the modification lands inside the index/working tree is the (unvalidated) start-line number carried in the header — a value that originated from whatever `git diff`/diff-producing plumbing emitted, not from anything GitHub Desktop independently verifies against the actual file content. Just as the Sway `pow` bug lets an out-of-range numeric result flow unchecked into a coin-amount calculation, an out-of-range/mismatched hunk-header number here flows unchecked into the line-offset that `git apply` uses to mutate the index.

### Impact Explanation
If the diff text Desktop parses does not faithfully reflect the real byte-for-byte content of the working tree at the declared offsets (e.g. output influenced by the target repository, such as a custom `.gitattributes` diff driver/textconv, or diff output whose hunk header doesn't match the following lines), Desktop will render a diff to the user that appears correct, but the actual `git apply --cached --unidiff-zero` operation used to build a partial commit or to discard a partial selection will write the change at the header's declared offset with no cross-check that this location matches the content shown to the user. This can result in staging or discarding content different from what the user selected — i.e., silent corruption of what the user commits or pushes, without any error being surfaced.

### Likelihood Explanation
Partial staging/discarding is a core, everyday Desktop feature exercised by every user who stages individual lines, so the vulnerable code path (`applyPatchToIndex` / `discardChangesFromSelection` → `formatPatch`/`formatPatchToDiscardChanges` → `git apply --cached --unidiff-zero`) is always reachable for any repository the user opens, including freshly cloned ones. The likelihood is limited by the need for the diff text feeding `DiffParser` to be crafted or manipulated (e.g. through repository-controlled diff configuration) so that header numbers don't match the true hunk content; this is a repository-side, not local-machine, precondition, matching the scope of "attacker controls a cloned/fetched repository."

### Recommendation
Validate, in `DiffParser.parseHunk`, that the number of context+delete lines actually parsed equals `header.oldLineCount` and that context+add lines equals `header.newLineCount`, rejecting/flagging the hunk if not. Additionally, when generating patches for `git apply --cached`, avoid `--unidiff-zero` where possible (retain some context lines) so git's own context-matching provides a safety check instead of relying solely on a self-reported offset, or independently verify the target lines in the index/working tree match the diff's declared old content before applying.

### Proof of Concept
Conceptual PoC (would need to be verified in a live Devin session with repository/file access, which isn't available here):
1. Prepare a repository (to be cloned/fetched by the victim) with a `.gitattributes` entry wiring a custom `diff=` driver or `textconv` filter for a tracked file, such that `git diff` output for that file reports hunk headers/line numbers that do not correspond 1:1 with the real file bytes at those offsets.
2. Victim clones the repo in GitHub Desktop, modifies the file, and stages only some of the lines shown in the (driver-altered) diff view.
3. Desktop calls `formatPatch` and `applyPatchToIndex`, which emit a patch using the driver-reported (untrusted) `oldStartLine`/`newStartLine` and apply it via `git apply --cached --unidiff-zero`.
4. Because `--unidiff-zero` accepts the hunk without context verification, the index ends up modified at the declared offset even though it doesn't correspond to the actual working-tree content the user intended to select, producing a committed/staged result silently different from what the user saw and chose in the diff view.

### Citations

**File:** app/src/lib/diff-parser.ts (L192-257)
```typescript
  /**
   * Attempts to convert a RegExp capture group into a number.
   * If the group doesn't exist or wasn't captured the function
   * will return the value of the defaultValue parameter or throw
   * an error if no default value was provided. If the captured
   * string can't be converted to a number an error will be thrown.
   */
  private numberFromGroup(
    m: RegExpMatchArray,
    group: number,
    defaultValue: number | null = null
  ): number {
    const str = m[group]
    if (!str) {
      if (!defaultValue) {
        throw new Error(
          `Group ${group} missing from regexp match and no defaultValue was provided`
        )
      }

      return defaultValue
    }

    const num = parseInt(str, 10)

    if (isNaN(num)) {
      throw new Error(
        `Could not parse capture group ${group} into number: ${str}`
      )
    }

    return num
  }

  /**
   * Parses a hunk header or throws an error if the given line isn't
   * a well-formed hunk header.
   *
   * We currently only extract the line number information and
   * ignore any hunk headings.
   *
   * Example hunk header (text within ``):
   *
   * `@@ -84,10 +82,8 @@ export function parseRawDiff(lines: ReadonlyArray<string>): Diff {`
   *
   * Where everything after the last @@ is what's known as the hunk, or section, heading
   */
  private parseHunkHeader(line: string): DiffHunkHeader {
    const m = diffHeaderRe.exec(line)
    if (!m) {
      throw new Error(`Invalid hunk header format`)
    }

    // If endLines are missing default to 1, see diffHeaderRe docs
    const oldStartLine = this.numberFromGroup(m, 1)
    const oldLineCount = this.numberFromGroup(m, 2, 1)
    const newStartLine = this.numberFromGroup(m, 3)
    const newLineCount = this.numberFromGroup(m, 4, 1)

    return new DiffHunkHeader(
      oldStartLine,
      oldLineCount,
      newStartLine,
      newLineCount
    )
  }
```

**File:** app/src/lib/patch-formatter.ts (L129-220)
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

**File:** app/src/lib/git/apply.ts (L102-120)
```typescript
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
