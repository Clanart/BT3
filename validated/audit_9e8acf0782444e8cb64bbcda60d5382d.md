### Title
Hidden bidirectional-Unicode ("Trojan Source") diff manipulation is only advisory and does not block staging/committing — silent corruption of committed content - (File: `app/src/lib/patch-formatter.ts`, `app/src/lib/git/update-index.ts`, `app/src/ui/diff/diff-contents-warning.tsx`)

### Summary
Desktop detects invisible bidirectional Unicode control characters in a diff (`HiddenBidiCharsRegex`) and sets `hasHiddenBidiChars` on the parsed diff [1](#0-0) [2](#0-1) . This flag is surfaced purely as a dismissible informational banner in the UI [3](#0-2) . Nothing in the staging or patch-generation path (`stageFiles`, `applyPatchToIndex`, `formatPatch`) consults this flag before writing the attacker-controlled `line.text` into the index/commit. The "check" exists, but the actual value that ends up committed (the raw hunk line text, including hidden bidi overrides) is taken and applied unconditionally, exactly mirroring the oracle report's pattern of validating one derived quantity while using an unvalidated raw quantity for the real effect.

### Finding Description
`formatPatch()` builds the actual unified-diff patch that is fed to `git apply --cached` by copying `line.text` verbatim for every selected line [4](#0-3) . That patch is applied directly to the index in `applyPatchToIndex()` [5](#0-4) , and for fully-selected files `stageFiles()` runs a plain `updateIndex`/`git add` instead of going through any diff review at all [6](#0-5) .

Separately, `hasHiddenBidiChars` is computed once when the diff is parsed and only ever consumed by the React `DiffContentsWarning` component to render an alert banner [7](#0-6) . There is no code path that reads `diff.hasHiddenBidiChars` (or an equivalent check) inside `stageFiles`, `applyPatchToIndex`, or `formatPatch` to prevent staging/committing, nor does it require any user acknowledgement/dismissal gate before the commit button is enabled. Additionally, when a diff is too large to render (`DiffType.LargeText`/`DiffType.Unrenderable`), the file is typically staged wholesale via `git add`/`update-index`, bypassing the hunk-level warning surface entirely since no `ITextDiff` (and therefore no `DiffContentsWarning`) is ever rendered for such files.

This is the structural analog of the reported bug class: a validated/derived signal (`hasHiddenBidiChars`, akin to the oracle price check) exists and is correct in isolation, but the quantity that actually determines the outcome (the raw patch bytes written to the index/commit, akin to `amount0`/`amount1`) is taken from unvalidated, attacker-influenced input and used without being gated by that check.

### Impact Explanation
An attacker who controls file content in a cloned/fetched repository (e.g., a fork a user is reviewing, or a repository they were asked to clone) can embed invisible bidi override characters (e.g., U+202E) to make a diff/file visually appear to do one thing while the bytes actually staged and committed do another (classic "Trojan Source" attack). Because the check is advisory-only, an unsuspecting user can stage, commit, and push code whose real semantics diverge from what was displayed — this is a silent corruption of what the user commits/pushes, one of the explicitly valid impact categories.

### Likelihood Explanation
Likelihood is moderate: the warning banner does reduce risk for reviewed, hunk-visible diffs, but it's easy to miss (small alert, not a blocking modal), does not apply to full-file "select all" staging flows or unrenderable/large diffs, and requires no unusual user action beyond the normal "stage all → commit" workflow that most users already perform.

### Recommendation
Gate staging/commit on `hasHiddenBidiChars` (and ideally line-ending mismatches) rather than treating them as purely cosmetic: require explicit user confirmation via a blocking dialog before `stageFiles`/`applyPatchToIndex` proceeds when hidden bidi characters are detected, and extend the same detection to the full-file `git add` path and to `LargeText`/`Unrenderable` diffs so large files aren't silently exempted from the check.

### Proof of Concept
1. Attacker crafts a repository file containing a line with an embedded U+202E (RIGHT-TO-LEFT OVERRIDE) so that the rendered diff line visually looks benign (e.g. `if (isAdmin) { ... }`) while the actual bytes reorder to change program logic (classic Trojan Source PoC, e.g. from the original CVE-2021-42574 disclosure).
2. Victim clones/fetches this repository into GitHub Desktop and opens Changes; `getWorkingDirectoryDiff` → `diffFromRawDiffOutput` sets `hasHiddenBidiChars: true` [2](#0-1) .
3. `DiffContentsWarning` renders a small, dismiss-and-forget alert banner above the diff [8](#0-7)  — it does not block the "Commit" button.
4. Victim selects the (visually benign-looking) lines and commits; `formatPatch` copies `line.text` — including the hidden override characters — verbatim into the patch [4](#0-3) , which is applied to the index via `git apply --cached` [5](#0-4)  and committed/pushed with the true (malicious) semantics intact, despite the on-screen review appearing safe.

### Citations

**File:** app/src/lib/diff-parser.ts (L25-30)
```typescript
/**
 * Regular expression matching invisible bidirectional Unicode characters that
 * may be interpreted or compiled differently than what it appears. More info:
 * https://github.co/hiddenchars
 */
export const HiddenBidiCharsRegex = /[\u202A-\u202E]|[\u2066-\u2069]/
```

**File:** app/src/lib/diff-parser.ts (L449-456)
```typescript
      return {
        header,
        contents,
        hunks,
        isBinary: headerInfo.isBinary,
        maxLineNumber: getLargestLineNumber(hunks),
        hasHiddenBidiChars: HiddenBidiCharsRegex.test(text),
      }
```

**File:** app/src/ui/diff/diff-contents-warning.tsx (L1-20)
```typescript
import React from 'react'
import { Octicon } from '../octicons'
import * as octicons from '../octicons/octicons.generated'
import { LinkButton } from '../lib/link-button'
import { ITextDiff, LineEndingsChange } from '../../models/diff'

enum DiffContentsWarningType {
  UnicodeBidiCharacters,
  LineEndingsChange,
}

type DiffContentsWarningItem =
  | {
      readonly type: DiffContentsWarningType.UnicodeBidiCharacters
    }
  | {
      readonly type: DiffContentsWarningType.LineEndingsChange
      readonly lineEndingsChange: LineEndingsChange
    }

```

**File:** app/src/ui/diff/diff-contents-warning.tsx (L25-63)
```typescript
export class DiffContentsWarning extends React.Component<IDiffContentsWarningProps> {
  public render() {
    const items = this.getTextDiffWarningItems()

    if (items.length === 0) {
      return null
    }

    return (
      <div className="diff-contents-warning-container">
        {items.map((item, i) => (
          <div className="diff-contents-warning" key={i}>
            <Octicon symbol={octicons.alert} />
            {this.getWarningMessageForItem(item)}
          </div>
        ))}
      </div>
    )
  }

  private getTextDiffWarningItems(): ReadonlyArray<DiffContentsWarningItem> {
    const items = new Array<DiffContentsWarningItem>()
    const { diff } = this.props

    if (diff.hasHiddenBidiChars) {
      items.push({
        type: DiffContentsWarningType.UnicodeBidiCharacters,
      })
    }

    if (diff.lineEndingsChange) {
      items.push({
        type: DiffContentsWarningType.LineEndingsChange,
        lineEndingsChange: diff.lineEndingsChange,
      })
    }

    return items
  }
```

**File:** app/src/lib/patch-formatter.ts (L157-171)
```typescript
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

**File:** app/src/lib/git/update-index.ts (L109-169)
```typescript
export async function stageFiles(
  repository: Repository,
  files: ReadonlyArray<WorkingDirectoryFileChange>
): Promise<void> {
  const normal = []
  const oldRenamed = []
  const partial = []
  const deletedFiles = []

  for (const file of files) {
    if (file.selection.getSelectionType() === DiffSelectionType.All) {
      normal.push(file.path)
      if (file.status.kind === AppFileStatusKind.Renamed) {
        oldRenamed.push(file.status.oldPath)
      } else if (file.status.kind === AppFileStatusKind.Deleted) {
        deletedFiles.push(file.path)
      }
    } else {
      partial.push(file)
    }
  }

  // Staging files happens in three steps.
  //
  // In the first step we run through all of the renamed files, or
  // more specifically the source files (old) that were renamed and
  // forcefully remove them from the index. We do this in order to handle
  // the scenario where a file has been renamed and a new file has been
  // created in its original position. Think of it like this
  //
  // $ touch foo && git add foo && git commit -m 'foo'
  // $ git mv foo bar
  // $ echo "I'm a new foo" > foo
  //
  // Now we have a file which is of type Renamed that has its path set
  // to 'bar' and its oldPath set to 'foo'. But there's a new file called
  // foo in the repository. So if the user selects the 'foo -> bar' change
  // but not the new 'foo' file for inclusion in this commit we don't
  // want to add the new 'foo', we just want to recreate the move in the
  // index. We do this by forcefully removing the old path from the index
  // and then later (in step 2) stage the new file.
  await updateIndex(repository, oldRenamed, { forceRemove: true })

  // In the second step we update the index to match
  // the working directory in the case of new, modified, deleted,
  // and copied files as well as the destination paths for renamed
  // paths.
  await updateIndex(repository, normal)

  // This third step will only happen if we have files that have been marked
  // for deletion. This covers us for files that were blown away in the last
  // updateIndex call
  await updateIndex(repository, deletedFiles, { forceRemove: true })

  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
}
```
