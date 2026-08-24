### Title
Partial-commit patches are generated from unfiltered `git diff` text and applied with `git apply --cached`, bypassing clean filters and silently corrupting what is actually committed - (File: `app/src/lib/patch-formatter.ts`, `app/src/lib/git/apply.ts`)

### Summary
When a user stages only some lines/hunks of a file ("partial commit"), Desktop does not use `git add -p`/`git add --patch` semantics. Instead it builds the patch itself in `formatPatch()` from the raw `IRawDiff`/`ITextDiff` line text produced earlier by `getWorkingDirectoryDiff`, and then applies that self-built patch to the index with `git apply --cached --unidiff-zero --whitespace=nowarn` in `applyPatchToIndex()`. `git apply` operates on the literal bytes of the patch text and does **not** invoke the repository's configured content filters (`clean`/`smudge`, `working-tree-encoding`, `ident`, `text=auto`/`eol` normalization) the way `git add`/`git commit` normally would when staging a whole file. This is the same class of bug as the Sherlock finding: a value computed for the user (the diff/patch that will be "committed") does not account for a transformation that the underlying system actually performs afterward (JIT penalty in Ammplify, filter/normalization in Git), so what the user sees and approves diverges from what actually lands in the object database.

### Finding Description
- `formatPatch()` (`app/src/lib/patch-formatter.ts:129-232`) builds the hunk text purely from the diff lines (`line.text`) that were parsed out of `git diff`'s output for the working tree, honoring only the user's line/hunk selection state. It never re-applies or accounts for any content filter defined via `.gitattributes` in the repository (e.g. `filter=lfs`, custom `clean`/`smudge` drivers, `working-tree-encoding`, or `text`/`eol` settings for CRLF normalization). [1](#0-0) 
- `applyPatchToIndex()` (`app/src/lib/git/apply.ts:12-84`) takes that self-built patch and feeds it directly to `git apply --cached --unidiff-zero --whitespace=nowarn -`, staging it straight into the index. [2](#0-1) 
- `stageFiles()` (`app/src/lib/git/update-index.ts:109-168`) routes any file with a non-`All` selection through this exact `applyPatchToIndex` path for every partial stage/commit, so this is the standard code path any time a user unchecks part of a file's diff before committing. [3](#0-2) 
- `git apply` (unlike `git add`) reads the patch text and writes blob content essentially verbatim; it does not run the repository's clean filters/`ident`/line-ending normalization the way `git add`/`git commit -a` do for a fully-staged file. If a cloned/fetched repository ships a `.gitattributes` that declares such a filter (a value fully controlled by the repo the user cloned or fetched, i.e. attacker-controlled), the diff Desktop displayed for review — and which the user believes reflects what will be committed — is not the content that will actually be written when partial hunks are staged this way. The displayed hunk selection UI gives the user (and any code review process) a false sense of exactly which lines/bytes will be committed.

This mirrors the Sherlock bug precisely: `queryAssetBalances` (a "preview" computation) omitted a transform (`applyJITPenalties`) that the real state-changing function (`removeMaker`) applies; here the Desktop "preview"/patch-construction step (`formatPatch`, built from displayed diff text) omits a transform (repository-defined content filters) that Git's normal staging path applies, but that this custom `git apply --cached` bypass does not reliably reproduce.

### Impact Explanation
This falls under "silent corruption of what the user commits or pushes," which is explicitly listed as a valid impact category. An attacker who controls a cloned/fetched repository (via `.gitattributes` filter/ident/eol declarations) can cause a user doing routine partial-hunk staging to commit and push content that differs from what was shown and approved in the Changes view — without any error or warning, since `git apply` will typically apply successfully. This could be leveraged to inject unexpected bytes/encoding into commits that the user believes only contain the reviewed diff.

### Likelihood Explanation
Requires: (1) the user opens a repository containing attacker-crafted `.gitattributes` filter/encoding/eol rules for a tracked file, and (2) the user performs a partial (hunk/line-level) commit on that file — a common, everyday Desktop workflow, not an unnatural user action. No admin rights, local access, or pre-existing malware are needed; the trigger is purely "clone/open a repo with a crafted `.gitattributes`" plus normal partial-staging usage. I was not able to execute Desktop's test suite in this environment to directly reproduce a byte-level mismatch for a specific filter type (e.g., `working-tree-encoding`), so the exact magnitude of corruption for each filter type is unverified from static review alone; this should be confirmed with a live repro against a repo declaring such attributes.

### Recommendation
- For files under partial selection, avoid constructing and applying a hand-rolled diff via `git apply --cached`; instead prefer `git stash`-based or `git add -p`-equivalent flows that go through Git's normal filter pipeline, or explicitly detect attribute-driven filters (`clean`/`smudge`, `ident`, `working-tree-encoding`, `text`/`eol`) on the file and fall back to full-file staging (or a warning) rather than a raw `git apply`.
- Alternatively, run the constructed patch content through `git hash-object --stdin --path <file>` (which does invoke `clean` filters) to build the blob, then use `git update-index --cacheinfo` to insert it, ensuring the staged content matches what filters would produce.
- Surface a warning to the user (similar to the existing filtered-changes-list warning) when a file with a partial selection has attribute-driven content filters, so users are alerted before believing the previewed diff is exactly what will be committed.

### Proof of Concept
Conceptual (not executed in this environment due to lack of terminal access):
1. Clone/open a repository containing `.gitattributes` with, e.g., `foo.txt text working-tree-encoding=UTF-16` (or a custom `clean`/`smudge` filter, or `eol=crlf` normalization) for a tracked file `foo.txt`.
2. Modify `foo.txt` in the working directory, causing Desktop to show a diff of the working-tree content.
3. In the Changes list, select only some lines/hunks of `foo.txt` (partial commit) and commit.
4. Desktop calls `stageFiles` → `applyPatchToIndex` → `formatPatch` (diff-text based) → `git apply --cached` (`app/src/lib/git/update-index.ts:163-168`, `app/src/lib/git/apply.ts:52-83`, `app/src/lib/patch-formatter.ts:129-232`).
5. Inspect the resulting committed blob (`git show :foo.txt` or `git cat-file -p`) versus what `git add`+`git commit` on the same selection with the filter engaged would have produced — expect the byte content/encoding to differ from what the review UI displayed, without any Desktop warning.

### Citations

**File:** app/src/lib/patch-formatter.ts (L129-161)
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

**File:** app/src/lib/git/update-index.ts (L163-168)
```typescript
  // Finally we run through all files that have partial selections.
  // We don't care about renamed or not here since applyPatchToIndex
  // has logic to support that scenario.
  for (const file of partial) {
    await applyPatchToIndex(repository, file)
  }
```
