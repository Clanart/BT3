Confirmed: `git status -z` output does not quote or escape special characters in filenames (including embedded newlines), so `entry.path` in `status-parser.ts` can contain raw newline bytes without any sanitization [1](#0-0) . This path is passed straight through `convertToAppStatus`/`buildStatusMap` into a `WorkingDirectoryFileChange` with no validation [2](#0-1) . `formatPatchHeader` then interpolates `from`/`to` directly into the `--- a/${from}` / `+++ b/${to}` lines with no escaping [3](#0-2) , and this patch is piped as stdin to `git apply --cached` in `applyPatchToIndex` [4](#0-3) .

### Title
Unescaped file paths in `formatPatchHeader` allow crafted-path patch/header injection into `git apply --cached` - (`app/src/lib/patch-formatter.ts`)

### Summary
`formatPatchHeader` builds unified-diff `---`/`+++` header lines by direct string interpolation of the file's path, with no escaping of newlines or diff-header-like substrings. Since `git status -z` (used by Desktop) never quotes or escapes filenames, an attacker who controls a repository's tracked filenames can commit a path containing a literal `\n` (and diff-header-like text after it) which will be passed to `formatPatch`/`formatPatchHeader` unmodified, and then piped into `git apply --cached` when the user stages an individual line/hunk via Desktop's partial-commit ("stage selected lines") UI.

### Finding Description
`formatPatchHeader` at `app/src/lib/patch-formatter.ts:22-36` does:
```
const fromPath = from ? `a/${from}` : '/dev/null'
const toPath = to ? `b/${to}` : '/dev/null'
return `--- ${fromPath}\n+++ ${toPath}\n`
```
No escaping is applied to `from`/`to`. These values ultimately originate from `WorkingDirectoryFileChange.path`, which is populated from `git status --porcelain=2 -z` output parsed by `parsePorcelainStatus`/`parseChangedEntry` etc. in `app/src/lib/status-parser.ts`. Per git's own documented behavior (quoted in a comment in that file), the `-z` format explicitly disables quoting/backslash-escaping of special characters in filenames, so a filename containing an embedded newline byte would appear as a literal `\n` in the parsed `path` string [1](#0-0) .

`applyPatchToIndex` calls `formatPatch(file, diff)` and feeds the resulting text as stdin to `git apply --cached --unidiff-zero --whitespace=nowarn -` [4](#0-3) . If the crafted path contains a newline followed by something resembling another diff header (e.g. `.../a\n+++ b/other-file`), the resulting stdin stream would contain an unintended extra header-like line inside what Desktop believes is a single, well-formed patch for one file.

### Likelihood/Impact Explanation
This is a real code smell (unescaped interpolation), but exploitability is constrained by git's own path validation rules and `git apply`'s patch-header parser, which I was not able to fully verify in this codebase (git's C implementation is out of scope for the index). Specifically:
- Git tree/index entries generally reject NUL bytes and `/` as path separators, but whether an embedded `\n` (0x0A) is actually accepted as part of a blob/tree path by `git mktree`/`update-index`/normal commit workflows needs confirmation — this determines whether the described attacker-controlled repository content is even constructible.
- Even if such a path can exist, `git apply`'s parser for the `--- `/`+++ ` unified-header pair has its own heuristics (e.g., it expects both lines together, validates prefixes, and typically also expects a `diff --git` line or consistent following hunk headers) that may reject or fail to reinterpret the injected text as a second file header, rather than silently writing to a different file.

Because I cannot verify git's acceptance of newline-containing paths or `git apply`'s exact parsing resilience against this specific crafted input from the code available in this repository/index, I can't confirm the "silently writes to an attacker-chosen second path" impact as described. This would require an actual PoC run against `git apply` (constructing a repo with such a filename, running Desktop's partial-stage flow, and observing which file(s) `git apply --cached` modifies) to confirm or refute.

### Recommendation
Regardless of exploitability confirmation, `formatPatchHeader` should defensively reject or escape paths containing newline characters (and other control characters) before building the patch text, and/or Desktop should validate `WorkingDirectoryFileChange.path` earlier in the status pipeline to reject anomalous byte sequences. This closes off the class of injection regardless of the outcome of the low-level git apply parsing question.

### Proof of Concept
Not verified end-to-end. A verification PoC would require: (1) confirming a git blob path containing a literal newline byte can be created and shows up via `git status --porcelain=2 -z`, and (2) running Desktop's "stage selected lines" flow against such a file and observing `git apply --cached`'s actual target file(s). This was not performed as part of this codebase review.

### Citations

**File:** app/src/lib/status-parser.ts (L65-72)
```typescript
  // There is also an alternate -z format recommended for machine parsing. In that
  // format, the status field is the same, but some other things change. First,
  // the -> is omitted from rename entries and the field order is reversed (e.g
  // from -> to becomes to from). Second, a NUL (ASCII 0) follows each filename,
  // replacing space as a field separator and the terminating newline (but a space
  // still separates the status field from the first filename). Third, filenames
  // containing special characters are not specially formatted; no quoting or
  // backslash-escaping is performed.
```

**File:** app/src/lib/git/status.ts (L329-348)
```typescript
  const appStatus = convertToAppStatus(
    entry.path,
    status,
    conflictDetails,
    entry.oldPath
  )

  const initialSelectionType =
    appStatus.kind === AppFileStatusKind.Modified &&
    appStatus.submoduleStatus !== undefined &&
    !appStatus.submoduleStatus.commitChanged
      ? DiffSelectionType.None
      : DiffSelectionType.All

  const selection = DiffSelection.fromInitialSelection(initialSelectionType)

  files.set(
    entry.path,
    new WorkingDirectoryFileChange(entry.path, appStatus, selection)
  )
```

**File:** app/src/lib/patch-formatter.ts (L22-36)
```typescript
function formatPatchHeader(from: string | null, to: string | null): string {
  // https://en.wikipedia.org/wiki/Diff_utility
  //
  // > At the beginning of the patch is the file information, including the full
  // > path and a time stamp delimited by a tab character.
  // >
  // > [...] the original file is preceded by "---" and the new file is preceded
  // > by "+++".
  //
  // We skip the time stamp to match git
  const fromPath = from ? `a/${from}` : '/dev/null'
  const toPath = to ? `b/${to}` : '/dev/null'

  return `--- ${fromPath}\n+++ ${toPath}\n`
}
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
