This confirms the key fact: Desktop explicitly test-cases and supports parsing filenames containing literal embedded newlines from `git status -z` output — the test at [1](#0-0)  proves a path with a `\n` in it round-trips intact through `parsePorcelainStatus`. This is the attacker-controlled primitive: a cloned/fetched repository can contain a working-directory file whose name embeds a newline (and arbitrary gitignore-glob syntax), and Desktop will display and act on that exact string.

The sink is `escapeGitSpecialCharacters`, which only escapes `[`, `]`, `!`, `*`, `#`, `?` and does **not** escape `\n` (or `/`): [2](#0-1) 

That escaped string is then joined into the `.gitignore` file content via `appendIgnoreRule` → `saveGitIgnore`, which writes it verbatim to disk: [3](#0-2) [4](#0-3) 

The UI path that lets a user "ignore" one of these attacker-named files is the changes-list context menu, which passes the raw path straight to `onIgnoreFile`: [5](#0-4) 

### Title
Unescaped newline in filenames allows `.gitignore` rule injection from a malicious cloned repository - (File: `app/src/lib/git/gitignore.ts`)

### Summary
`escapeGitSpecialCharacters()` in `app/src/lib/git/gitignore.ts` is meant to neutralize gitignore-pattern metacharacters before a user-selected working-directory path is appended to `.gitignore`. It escapes `[`, `]`, `!`, `*`, `#`, `?` but not the newline character (`\n`). Because `git status -z` output (parsed by `parsePorcelainStatus` in `app/src/lib/status-parser.ts`) is NUL-delimited, a tracked/untracked file whose name legitimately contains an embedded newline is parsed and surfaced to the UI with the newline intact — this is explicitly covered by an existing unit test. When such a file is right-clicked and "Ignore file" is chosen, the unescaped newline splits into a brand-new line in `.gitignore`, letting the attacker-controlled filename inject an arbitrary, attacker-chosen ignore pattern (e.g. a broad glob like `*.go` or `src/`) that the escaping function never sanitizes because it treats the newline as ordinary content rather than a line-terminator/metacharacter.

### Finding Description
`escapeGitSpecialCharacters` (`app/src/lib/git/gitignore.ts:103-109`) is the only sanitization step applied to a working-directory path before it becomes a literal `.gitignore` line via `appendIgnoreFile` → `appendIgnoreRule` → `saveGitIgnore`. The regex `/[\[\]!\*\#\?]/g` does not include `\n`, `\r`, or `/`. Filenames are sourced from `git status --porcelain=2 -z` and parsed by `parsePorcelainStatus`, which splits on NUL bytes, not newlines, so a path containing an embedded `\n` is preserved verbatim as demonstrated by the existing test case "parses a path which includes a newline" (`app/test/unit/status-parser-test.ts:95-107`). A cloned/fetched repository can therefore contain a file whose name is, for example, `harmless\n*.env` (newline is a valid character in POSIX filenames, only NUL and `/` are forbidden). If the victim opens Desktop, sees this file in the Changes list, and uses the ordinary "Ignore file (Add to .gitignore)" context-menu action (`app/src/ui/changes/filter-changes-list.tsx:698-706`), Desktop writes the unescaped string to `.gitignore`, and the embedded newline creates a second, attacker-chosen ignore pattern line. Existing guards do not stop this: `escapeGitSpecialCharacters` only strips gitignore glob metacharacters assuming the entire string will occupy one line, and neither `appendIgnoreRule` nor `saveGitIgnore` validate or single-line-normalize their input before writing to disk.

### Impact Explanation
The injected pattern silently changes what git considers trackable, causing legitimate files to be excluded from `git add`/commit without an explicit user choice for that pattern — a form of silent corruption of what the user commits, since files matching the injected rule will stop appearing as changes and can be dropped from future commits without the user noticing the additional rule was added on their behalf.

### Likelihood Explanation
Exploitation requires only that the victim clone/fetch a repository containing a maliciously-named file and use a standard, common Desktop feature ("Ignore file") on it — no admin rights, no local access, and no unnatural steps beyond normal repository browsing are needed. The barrier is that the attacker must guess or target a specific "Ignore file" action rather than it firing automatically, which somewhat lowers likelihood but the primitive (newline surviving intact through git status parsing) is proven in-repo by the existing test.

### Recommendation
Extend `escapeGitSpecialCharacters` (or add a dedicated check in `appendIgnoreFile`/`appendIgnoreRule`) to also escape or reject `\n` and `\r` characters (and ideally validate there is exactly one resulting line) before the value is persisted to `.gitignore`.

### Proof of Concept
1. Attacker creates a repository containing a file literally named `safe\n*.secret` (newline embedded in the filename) and pushes/hosts it for cloning.
2. Victim clones the repository in GitHub Desktop; the file appears in the Changes list as an untracked/changed entry with the embedded newline preserved (per `parsePorcelainStatus` behavior, confirmed by `app/test/unit/status-parser-test.ts:95-107`).
3. Victim right-clicks the entry and selects "Ignore file (Add to .gitignore)" (`filter-changes-list.tsx:698-706`).
4. `escapeGitSpecialCharacters` escapes none of the newline, so `appendIgnoreRule` writes two lines to `.gitignore`: `safe\n` and `*.secret\n`.
5. Any future `*.secret` file in the working tree is now silently ignored by git without the victim ever intentionally adding that rule.

### Citations

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

**File:** app/src/lib/git/gitignore.ts (L62-79)
```typescript
/** Add the given pattern or patterns to the root gitignore file */
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

**File:** app/src/lib/git/gitignore.ts (L102-109)
```typescript
/** Escapes a string from special characters used in a gitignore file */
export function escapeGitSpecialCharacters(pattern: string): string {
  const specialCharacters = /[\[\]!\*\#\?]/g

  return pattern.replaceAll(specialCharacters, match => {
    return '\\' + match
  })
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
