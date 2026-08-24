## Analysis

`git status -z` (used in `getStatus`, [1](#0-0) ) NUL-separates filenames and, per the code comment itself, performs **no quoting or backslash-escaping** even for filenames with special characters — this includes embedded newlines ( [2](#0-1) ). The parser (`splitBuffer` on `\0`) therefore can produce an `IStatusEntry.path` that legitimately contains a raw `\n` byte, e.g. an untracked file created on disk from a checked-out repository whose tree entry name embeds `\n` (permitted on POSIX filesystems, where only `/` and NUL are forbidden in filenames). That path flows into `WorkingDirectoryFileChange.path` ( [3](#0-2) ), and can reach `Dispatcher.appendIgnoreFile` → `AppStore._appendIgnoreFile` → `appendIgnoreFile` ( [4](#0-3) , [5](#0-4) ) when the user selects "Ignore file (add to .gitignore)" from the context menu ( [6](#0-5) ).

`escapeGitSpecialCharacters` only escapes `[`, `]`, `!`, `*`, `#`, `?` via `/[\[\]!\*\#\?]/g` and never touches `\n` ( [7](#0-6) ). `appendIgnoreRule` then simply concatenates the (still newline-containing) escaped string into the `.gitignore` text ( [8](#0-7) ) and `formatGitIgnoreContents`/`saveGitIgnore` write it verbatim to disk ( [9](#0-8)  , [10](#0-9) ). Since `.gitignore` is line-oriented, an embedded `\n` splits the appended pattern into two separate ignore lines, and the attacker fully controls the content after the injected newline (e.g. `!secret.txt`, or a line un-ignoring/ignoring arbitrary paths).

The existing unit tests ( [11](#0-10)  and [12](#0-11) ) only cover `[`, `]`, `!`, `*`, `#`, `?` — none exercise a filename containing `\n`, confirming this gap is not caught by existing checks.

This confirms the mechanism described in the question is real at the code level: the escaping regex never matches `\n`, so a crafted filename can inject an attacker-chosen extra `.gitignore` line. However, the trigger requires the user to explicitly right-click the untracked file and choose "Ignore file" — it is not automatic on clone/fetch — which is a normal, foreseeable interaction with attacker-supplied untracked content, not an "unnatural" step.

### Title
Newline injection via `escapeGitSpecialCharacters` allows attacker-controlled `.gitignore` rule injection - (File: `app/src/lib/git/gitignore.ts`)

### Summary
`escapeGitSpecialCharacters` fails to escape `\n`, allowing a maliciously named untracked file (containing an embedded LF) from an attacker-controlled repository to inject an extra, unescaped `.gitignore` rule when the user uses the "Ignore file" context-menu action.

### Finding Description
`git status -z` returns raw, unescaped file paths for untracked entries, including any embedded newline byte present in the on-disk filename ( [2](#0-1) , [13](#0-12) ). `appendIgnoreFile` escapes only `[]!*#?` via `escapeGitSpecialCharacters`, leaving `\n` intact ( [7](#0-6) ). `appendIgnoreRule` appends this string directly to the `.gitignore` contents ( [8](#0-7) ), so the embedded `\n` becomes a real line break in the written file, letting the attacker append a second, fully attacker-controlled ignore rule (e.g., `!` un-ignore rules or broad glob patterns).

### Impact Explanation
This causes silent corruption of the user's `.gitignore`, which determines what gets committed/pushed — matching the "silent corruption of what the user commits or pushes" impact category. An attacker could cause sensitive files to become untracked/ignored (hiding malicious files from `git status`/diff review) or unignore previously ignored sensitive paths, depending on the injected rule.

### Likelihood Explanation
Requires the victim to clone/fetch a malicious repository containing a file with an embedded newline in its name and then manually invoke "Ignore file (add to .gitignore)" from the Changes list context menu on that specific untracked file — a plausible but non-default user action.

### Recommendation
Extend `escapeGitSpecialCharacters` (or add a dedicated sanitization step in `appendIgnoreFile`) to also escape/reject `\n` and `\r` (and ideally NUL, though NUL can't appear in a path) before joining patterns, e.g. escaping `\n` as `\\n`-literal text is not valid gitignore syntax, so the safest approach is to reject or strip control characters from `filePath` entries before use, or refuse to add a gitignore rule for paths containing newlines.

### Proof of Concept
1. Clone/create a repository containing a tracked or untracked file whose name is `x\n!secret.txt` (creatable on POSIX filesystems via a git tree entry with an embedded LF, or directly via `fs.writeFileSync('x\n!secret.txt', ...)`).
2. In Desktop, right-click that entry in the Changes list and choose "Ignore file (add to .gitignore)", which calls `appendIgnoreFile(repository, 'x\n!secret.txt')` ( [4](#0-3) ).
3. Inspect `.gitignore`: it now contains two lines — `x` and `!secret.txt` — instead of one escaped literal line, because `escapeGitSpecialCharacters` never escaped the `\n` ( [14](#0-13) ).

### Citations

**File:** app/src/lib/git/status.ts (L212-224)
```typescript
  const args = [
    '--no-optional-locks',
    'status',
    ...(includeUntracked ? ['--untracked-files=all'] : []),
    '--branch',
    '--porcelain=2',
    '-z',
  ]

  const { stdout, exitCode } = await git(args, repository.path, 'getStatus', {
    successExitCodes: new Set(rejectOnError ? [0] : [0, 128]),
    encoding: 'buffer',
  })
```

**File:** app/src/lib/git/status.ts (L345-348)
```typescript
  files.set(
    entry.path,
    new WorkingDirectoryFileChange(entry.path, appStatus, selection)
  )
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

**File:** app/src/lib/status-parser.ts (L172-182)
```typescript
function parseUntrackedEntry(field: string): IStatusEntry {
  const path = field.substring(2)
  return {
    kind: 'entry',
    // NOTE: We return ?? instead of ? here to play nice with mapStatus,
    // might want to consider changing this (and mapStatus) in the future.
    statusCode: '??',
    submoduleStatusCode: '????',
    path,
  }
}
```

**File:** app/src/ui/changes/sidebar.tsx (L269-271)
```typescript
  private onIgnoreFile = (file: string | string[]) => {
    this.props.dispatcher.appendIgnoreFile(this.props.repository, file)
  }
```

**File:** app/src/lib/stores/app-store.ts (L7937-7943)
```typescript
  public async _appendIgnoreFile(
    repository: Repository,
    filePath: string | string[]
  ): Promise<void> {
    await appendIgnoreFile(repository, filePath)
    return this._refreshRepository(repository)
  }
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

**File:** app/src/lib/git/gitignore.ts (L40-60)
```typescript
export async function saveGitIgnore(
  repository: Repository,
  text: string
): Promise<void> {
  const ignorePath = Path.join(repository.path, '.gitignore')

  if (text === '') {
    return new Promise<void>((resolve, reject) => {
      FS.unlink(ignorePath, err => {
        if (err) {
          reject(err)
        } else {
          resolve()
        }
      })
    })
  }

  const fileContents = await formatGitIgnoreContents(text, repository)
  await writeFile(ignorePath, fileContents)
}
```

**File:** app/src/lib/git/gitignore.ts (L63-79)
```typescript
export async function appendIgnoreRule(
  repository: Repository,
  patterns: string | string[]
): Promise<void> {
  const text = (await readGitIgnoreAtRoot(repository)) || ''

  const currentContents = await formatGitIgnoreContents(text, repository)

  const newPatternText =
    patterns instanceof Array ? patterns.join('\n') : patterns
  const newText = await formatGitIgnoreContents(
    `${currentContents}${newPatternText}`,
    repository
  )

  await saveGitIgnore(repository, newText)
}
```

**File:** app/src/lib/git/gitignore.ts (L98-109)
```typescript
  const escapedFilePath = escapeGitSpecialCharacters(filePath)
  return appendIgnoreRule(repository, escapedFilePath)
}

/** Escapes a string from special characters used in a gitignore file */
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

**File:** app/test/unit/git/gitignore-test.ts (L140-146)
```typescript
    it('escapes string with special git characters', async () => {
      const unescapedFilePath = '[never]\\!gonna*give#you?_.up'
      const escapedFilePath = '\\[never\\]\\\\!gonna\\*give\\#you\\?_.up'

      const result = escapeGitSpecialCharacters(unescapedFilePath)
      assert.equal(result, escapedFilePath)
    })
```

**File:** app/test/unit/git/gitignore-test.ts (L186-204)
```typescript
    it('appends one file containing special characters', async t => {
      const repo = await setupEmptyRepository(t)

      await setupLocalConfig(repo, [['core.autocrlf', 'true']])

      const { path } = repo

      const ignoreFile = `${path}/.gitignore`
      await writeFile(ignoreFile, 'node_modules\n')

      const fileToIgnore = '[never]!gonna*give#you?_.up'
      await appendIgnoreFile(repo, [fileToIgnore])

      const gitignore = await readFile(ignoreFile)

      const expected =
        'node_modules\n' + '\\[never\\]\\!gonna\\*give\\#you\\?_.up\n'
      assert.equal(gitignore.toString('utf8'), expected)
    })
```
