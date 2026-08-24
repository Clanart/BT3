## Title
`.gitignore` injection via unescaped newline/CR in attacker-controlled filenames — silently alters ignore rules (`app/src/lib/git/gitignore.ts`)

## Summary
`escapeGitSpecialCharacters` only escapes glob metacharacters (`[ ] ! * # ?`) and never escapes line-break characters (`\n`, `\r`). Git's `status --porcelain -z` output explicitly performs no quoting/escaping of filenames, so a file name containing an embedded `\n`/`\r` from a malicious repository is preserved verbatim all the way into `WorkingDirectoryFileChange.path` and then into `appendIgnoreFile`. Because the escaping function does not neutralize the line break, an attacker-crafted file name effectively injects one or more arbitrary additional lines into the user's `.gitignore` when the user performs the ordinary "Ignore file" context-menu action.

## Finding Description
`escapeGitSpecialCharacters` is defined as: [1](#0-0) 

It is applied to file paths before they are appended to `.gitignore` by `appendIgnoreFile`: [2](#0-1) 

The escaped/joined pattern text is then only newline-normalized (not sanitized) by `formatGitIgnoreContents`, which either leaves embedded `\n`/`\r` as-is or converts existing line-break sequences to `\r\n` depending on `core.autocrlf`/`core.safecrlf`, but never removes or escapes them: [3](#0-2) 

The `path` value that reaches `appendIgnoreFile` comes straight from parsing `git status --porcelain=2 -z`, and the parser's own comment plus a dedicated unit test confirm that filenames with embedded newlines are passed through unmodified: [4](#0-3) [5](#0-4) 

The normal UI flow for this is the "Ignore file (add to .gitignore)" context-menu entry in the Changes list, which passes the raw file path straight through: [6](#0-5) [7](#0-6) 

Because `\n`/`\r` are not escaped, a file whose name is, e.g., `innocuous.txt\n!secret.key`, results in `.gitignore` gaining two lines instead of one escaped literal line — the second line being an attacker-chosen gitignore pattern (e.g., a negation `!pattern` that un-ignores a previously ignored sensitive path, or a broad pattern like `*.key` that silently hides new sensitive files from the Changes view so the user never stages/commits them, or conversely un-hides build artifacts causing them to be committed/pushed unexpectedly).

## Impact Explanation
This allows a malicious repository to silently corrupt the victim's `.gitignore` beyond what the user intended when they click "Ignore file" for a single, seemingly innocuous entry — changing which files are tracked/ignored without the user's knowledge. This matches the "silent corruption of what the user commits or pushes" impact category: the attacker can cause secret files to stop being tracked (never noticed as an untracked/changed file and thus never protected/reviewed) or cause previously-ignored files to reappear and get committed/pushed.

## Likelihood Explanation
Requires the victim to clone/fetch a malicious repository containing a file with an embedded `\n`/`\r` in its name (permitted by git tree entries, which forbid only `/` and NUL) and to perform the ordinary, commonly-used "Ignore file" context-menu action on that specific file. This is a normal, expected user interaction (not an unnatural or contrived step), making exploitation plausible for a targeted malicious repository.

## Recommendation
Extend `escapeGitSpecialCharacters` to also escape or strip carriage-return/line-feed characters (and reject/normalize any path containing them) before it is written into `.gitignore`, e.g. escape `\r` and `\n` or throw/refuse to add a pattern containing them, ensuring exactly one line is ever added per selected file regardless of the byte content of the underlying filename.

## Proof of Concept
1. Craft a repository containing a file named (as raw bytes) `innocuous.txt\n!secret.key` (newline embedded in the git tree entry name — permitted since git only forbids `/` and NUL in path components).
2. Victim clones the repository in GitHub Desktop; the file shows up as untracked in the Changes list with `path` equal to the raw two-line string (per `parsePorcelainStatus`/`parseUntrackedEntry` behavior, confirmed by the existing "parses a path which includes a newline" test).
3. Victim right-clicks the entry and selects "Ignore file (add to .gitignore)", invoking `onIgnoreFile(path)` → `dispatcher.appendIgnoreFile` → `appendIgnoreFile(repository, filePath)`.
4. `escapeGitSpecialCharacters` escapes `!` only if it were the first char of the whole string, but does not touch the embedded `\n`; the resulting `.gitignore` ends up containing:
```
innocuous.txt
!secret.key
```
instead of the intended single escaped literal line, silently un-ignoring/re-including `secret.key` (or, with a different chosen suffix, silently hiding arbitrary future files from Changes/commit).

### Citations

**File:** app/src/lib/git/gitignore.ts (L86-100)
```typescript
export async function appendIgnoreFile(
  repository: Repository,
  filePath: string | string[]
): Promise<void> {
  if (filePath instanceof Array) {
    const escapedFilePaths = filePath.map(path =>
      escapeGitSpecialCharacters(path)
    )

    return appendIgnoreRule(repository, escapedFilePaths)
  }

  const escapedFilePath = escapeGitSpecialCharacters(filePath)
  return appendIgnoreRule(repository, escapedFilePath)
}
```

**File:** app/src/lib/git/gitignore.ts (L103-109)
```typescript
export function escapeGitSpecialCharacters(pattern: string): string {
  const specialCharacters = /[\[\]!\*\#\?]/g

  return pattern.replaceAll(specialCharacters, match => {
    return '\\' + match
  })
}
```

**File:** app/src/lib/git/gitignore.ts (L125-157)
```typescript
async function formatGitIgnoreContents(
  text: string,
  repository: Repository
): Promise<string> {
  const autocrlf = await getConfigValue(repository, 'core.autocrlf')
  const safecrlf = await getConfigValue(repository, 'core.safecrlf')

  return new Promise<string>((resolve, reject) => {
    if (autocrlf === 'true' && safecrlf === 'true') {
      // based off https://stackoverflow.com/a/141069/1363815
      const normalizedText = text.replace(/\r\n|\n\r|\n|\r/g, '\r\n')
      resolve(normalizedText + '\r\n')
      return
    }

    if (text === '' || text.endsWith('\n')) {
      resolve(text)
      return
    }

    if (autocrlf == null) {
      // fallback to Git default behaviour
      resolve(`${text}\n`)
    } else {
      const linesEndInCRLF = autocrlf === 'true'
      if (linesEndInCRLF) {
        resolve(`${text}\n`)
      } else {
        resolve(`${text}\r\n`)
      }
    }
  })
}
```

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

**File:** app/test/unit/status-parser-test.ts (L95-107)
```typescript
  it('parses a path which includes a newline', () => {
    const x = `1 D. N... 100644 000000 000000 dc9fb24e86f7445720b39dcb39a7fc0e410d9583 0000000000000000000000000000000000000000 ProjectSID/Images.xcassets/iPhone 67/Status Center/Report X68 Y461
      /.DS_Store`
    const entries = parse(x) as ReadonlyArray<IStatusEntry>

    assert.equal(entries.length, 1)

    const expectedPath = `ProjectSID/Images.xcassets/iPhone 67/Status Center/Report X68 Y461
      /.DS_Store`

    assert.equal(entries[0].path, expectedPath)
    assert.equal(entries[0].statusCode, 'D.')
  })
```

**File:** app/src/ui/changes/filter-changes-list.tsx (L698-706)
```typescript
    if (paths.length === 1) {
      const enabled = Path.basename(path) !== GitIgnoreFileName
      items.push({
        label: __DARWIN__
          ? 'Ignore File (Add to .gitignore)'
          : 'Ignore file (add to .gitignore)',
        action: () => this.props.onIgnoreFile(path),
        enabled,
      })
```

**File:** app/src/ui/changes/sidebar.tsx (L269-271)
```typescript
  private onIgnoreFile = (file: string | string[]) => {
    this.props.dispatcher.appendIgnoreFile(this.props.repository, file)
  }
```
