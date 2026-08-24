## Title
Newline injection into `.gitignore` via unescaped filenames from cloned repositories — silently corrupts what the user commits ([File: app/src/lib/git/gitignore.ts])

## Summary
The "Ignore File" feature builds a `.gitignore` entry from a file's on-disk path and only escapes a small, fixed set of glob metacharacters. It never escapes or rejects the newline character (`\n`), even though Git permits arbitrary bytes other than `/` and `NUL` in tracked path names, and GitHub Desktop's own status parser explicitly supports multi-line paths. A file inside a cloned/fetched repository whose name embeds a newline followed by an attacker-chosen gitignore pattern (e.g. `notes\n*.env`) will, when the user performs the ordinary "Ignore File" action from the Changes list context menu, cause that attacker-chosen line to be injected verbatim as a brand-new, independent rule in the victim's `.gitignore`. This can silently start excluding files the user did not intend to exclude (e.g. `*.env`, `*.key`, or via a leading `!` even *un-ignoring* previously-ignored sensitive files), corrupting what gets committed/pushed from that point on without any warning to the user.

## Finding Description
`escapeGitSpecialCharacters` only escapes `[`, `]`, `!`, `*`, `#`, `?`: [1](#0-0) 

This function is applied to file paths right before they're persisted to `.gitignore`: [2](#0-1) 

`appendIgnoreRule` simply joins the (already-escaped) strings with `\n` and writes the resulting content to disk with `formatGitIgnoreContents`, which only normalizes line endings — it performs no additional escaping and does not reject embedded newlines: [3](#0-2) [4](#0-3) 

The `path` value that reaches this code comes directly from the entries produced by parsing `git status --porcelain=2 -z`, and Desktop's own tests confirm that a path containing a raw newline is parsed and preserved intact as a single `path` field (since the `-z` mode separates fields with NUL, not newline): [5](#0-4) 

The UI wires the selected file's `path` straight into `onIgnoreFile` from a normal right-click context menu action, with no length/character validation: [6](#0-5) [7](#0-6) 

Because the escaping only strips glob metacharacters and never touches `\n`, any newline embedded in the file path is written through unmodified, splitting the resulting `.gitignore` content into two (or more) separate rules — the second (and beyond) being fully attacker-controlled text.

**Broken invariant:** the code assumes "escaping the pattern is sufficient to make it safe to append as a single logical `.gitignore` line." That invariant silently breaks the moment the untrusted input contains a newline, exactly analogous to the audited bug's flawed assumption ("the CrossDomainMessenger is replayable so a gas limit of 0 is safe") — a single-purpose guard (glob-character escaping / minGasLimit-via-replay) covers the common case but not the boundary condition that turns a benign-looking operation into unbounded/unintended state mutation.

## Impact Explanation
An attacker who controls (or contributes to) a cloned/fetched repository can plant a committed file whose name is, for example:
```
notes\n*.env
```
When the victim opens this repository in Desktop, sees the file in the Changes/History list, and performs the everyday "Ignore File (Add to .gitignore)" action (a completely ordinary, expected workflow step — no unnatural steps required), Desktop appends:
```
notes
*.env
```
to `.gitignore`. From that point forward, any `.env` files in the victim's working copy are silently excluded from `git add`/commit, without any dialog, warning, or diff review surfacing this change as suspicious (it just looks like the normal "ignore" action the user requested). Depending on the injected pattern, this can:
- Silently drop security-relevant files (`.env`, `id_rsa`, CI secrets) from future commits (data/config loss, but also potential downstream security issues if the victim assumes those files are still tracked/protected).
- Use a negation pattern (`!secret-backdoor.txt`) to un-ignore and thus surface something previously hidden.
- More broadly, corrupt what the user commits/pushes without their knowledge — matching the "silent corruption of what the user commits or pushes" impact category.

## Likelihood Explanation
- No local/physical access, no admin rights, no pre-existing malware, and no leaked credentials are required — only a malicious/compromised repository the victim clones or fetches, which is squarely within GitHub Desktop's threat model for repository content.
- The triggering user action ("Ignore File" from the context menu) is a first-class, commonly used Desktop feature, not a contrived or unnatural step.
- POSIX filesystems (Linux, macOS) allow embedding `\n` in file/directory names, and Git itself has no restriction against storing such paths in a tree object, so the crafted payload is fully realizable.
- Desktop's own `-z`-based status parsing is explicitly designed to tolerate and preserve embedded newlines in paths (confirmed by its own unit test), meaning the payload survives intact from disk through to the vulnerable escaping function.

## Recommendation
In `escapeGitSpecialCharacters` (or before calling `appendIgnoreRule`/`appendIgnoreFile`), reject or escape newline/carriage-return characters (and ideally any other line-breaking control characters) in file paths before they are turned into `.gitignore` rule text. A safe approach is to refuse to build an ignore rule (and surface an error/warn dialog) for any path containing `\n`/`\r`, since such a path cannot be represented as a single valid, safe gitignore line.

## Proof of Concept
1. Attacker creates a repository containing a file named literally:
   `payload<NEWLINE>*.env` (i.e., a single path component containing a raw `\n` byte followed by `*.env`), and commits it. On POSIX this is a valid Git tree entry name.
2. Victim clones this repository in GitHub Desktop.
3. Victim selects that file in the Changes list, right-clicks, and chooses "Ignore File (Add to .gitignore)" — a completely normal action, invoking `onIgnoreFile` → `dispatcher.appendIgnoreFile` → `appendIgnoreFile` in `app/src/lib/git/gitignore.ts`.
4. `escapeGitSpecialCharacters` escapes `*` in `*.env` but does not touch the embedded `\n`, so `appendIgnoreRule` writes two lines to `.gitignore`:
   ```
   payload
   *.env
   ```
5. From this point on, any `.env` files in the victim's working directory are silently excluded from staging/commits — verifiable by creating a `.env` file and observing it never appears in the Changes list, with no warning shown to the user that this rule was added as a side effect of an unrelated "ignore" click.

### Citations

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

**File:** app/src/lib/git/gitignore.ts (L81-100)
```typescript
/**
 * Convenience method to add the given file path(s) to the repository's gitignore.
 *
 * The file path will be escaped before adding.
 */
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

**File:** app/src/lib/git/gitignore.ts (L111-157)
```typescript
/**
 * Format the gitignore text based on the current config settings.
 *
 * This setting looks at core.autocrlf to decide which line endings to use
 * when updating the .gitignore file.
 *
 * If core.safecrlf is also set, adding this file to the index may cause
 * Git to return a non-zero exit code, leaving the working directory in a
 * confusing state for the user. So we should reformat the file in that
 * case.
 *
 * @param text The text to format.
 * @param repository The repository associated with the gitignore file.
 */
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

**File:** app/src/ui/changes/filter-changes-list.tsx (L694-706)
```typescript
    const items: IMenuItem[] = [
      this.getDiscardChangesMenuItem(paths),
      { type: 'separator' },
    ]
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
