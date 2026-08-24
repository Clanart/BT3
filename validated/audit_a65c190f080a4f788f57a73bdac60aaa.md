## Analysis

`escapeGitSpecialCharacters` only escapes a fixed set of gitignore glob metacharacters: [1](#0-0) 

It does not escape a trailing space, and per Git's own `.gitignore` semantics, "Trailing spaces are ignored unless they are quoted with a backslash." This function is the only sanitization applied before a user-chosen file path is written into `.gitignore` via `appendIgnoreFile` → `appendIgnoreRule` → `saveGitIgnore`: [2](#0-1) 

The path that reaches `escapeGitSpecialCharacters` originates from `WorkingDirectoryFileChange.path`, which is populated straight from `git status` output for files present in the attacker's repository, and is wired to the "Ignore file"/"Ignore folder" context-menu actions: [3](#0-2) [4](#0-3) 

Regarding the two specific concerns in the question:

- **Leading `/`**: paths returned by `git status` are always repo-relative without a leading `/`; the only place a leading `/` is synthesized is the "Ignore Folder" submenu, which intentionally builds `/${pathComponents...}` as an anchored folder pattern — that's expected gitignore syntax, not attacker-controlled data flowing through `escapeGitSpecialCharacters`. This part is not exploitable through this path.
- **Trailing space**: a crafted repository (attacker-controlled clone/fetch content) can contain a file whose actual on-disk name ends with a space (valid on Linux/macOS filesystems). If the victim right-clicks that file and chooses "Ignore file," `escapeGitSpecialCharacters` passes the trailing space through unescaped. Git then parses that gitignore line by trimming the (unescaped) trailing space, so the pattern actually stored differs from the file name the user intended to ignore. Only the existing tests validate escaping of `[ ] ! * # ?`, with no coverage for trailing whitespace: [5](#0-4) 

This is a genuine, narrow gap in `escapeGitSpecialCharacters`, but the resulting impact is limited: it only produces a mismatched/ineffective ignore pattern for the specific mistrusted, trailing-space-named file the user explicitly chose to ignore via a normal, expected UI action; it does not let the attacker force Desktop to silently rewrite `.gitignore` against the user's will, nor does it let an attacker "de-ignore" an arbitrary hook/config file without the victim first invoking "Ignore file" on the crafted path themselves.

### Title
Unescaped trailing space in `escapeGitSpecialCharacters` causes gitignore pattern semantics to diverge from intended file - (File: `app/src/lib/git/gitignore.ts`)

### Summary
`escapeGitSpecialCharacters` escapes `[ ] ! * # ?` but not a trailing space, even though Git strips unescaped trailing spaces when parsing `.gitignore`. A crafted repository containing a file whose name ends in a space, when ignored via Desktop's "Ignore file" context menu, results in a `.gitignore` entry whose effective pattern (after Git trims the trailing space) no longer matches the file that was actually selected.

### Finding Description
`appendIgnoreFile` escapes `filePath` with `escapeGitSpecialCharacters` before appending it as a gitignore line via `appendIgnoreRule`/`saveGitIgnore`. The escape function's character class (`/[\[\]!\*\#\?]/g`) omits the space character, so a trailing space in the input passes through untouched. Because Git's `.gitignore` parser trims unescaped trailing whitespace from each pattern line, the persisted rule's effective matching semantics differ from the literal file name the user right-clicked. [1](#0-0) 

### Impact Explanation
The immediate effect is a silent divergence between the ignore rule the user intended (ignore exactly the crafted, space-suffixed file) and the rule that is actually enforced (ignore the name with the trailing space stripped). In an adversarial layout where an attacker-controlled repo contains both `name` and `name ` (trailing space), this can cause Desktop to end up ignoring the wrong file, or leaving the intended file un-ignored, changing which paths are tracked/ignored without the user noticing. This is a real but low-severity data-integrity issue confined to gitignore bookkeeping; it does not itself achieve code execution, credential exfiltration, or an IPC/sandbox escape.

### Likelihood Explanation
Requires an attacker to plant a file with a trailing-space name in a repository the victim clones/fetches, and requires the victim to explicitly choose "Ignore file (add to .gitignore)" on that specific file — a normal but necessary user action. Feasible on Linux/macOS filesystems; less relevant on Windows where such names are harder to materialize on checkout.

### Recommendation
Extend `escapeGitSpecialCharacters` (or add a dedicated step) to escape trailing whitespace, e.g. escape the last character with a backslash if it is a space, matching Git's own quoting rule, in addition to the existing special-character class.

### Proof of Concept
1. Create a test repository containing an untracked file literally named `evil ` (with a trailing space) — reachable on Linux/macOS.
2. Call `appendIgnoreFile(repo, 'evil ')` (as triggered by the "Ignore file" context-menu action in `app/src/ui/changes/sidebar.tsx`).
3. Inspect the resulting `.gitignore`: the appended line is `evil ` (unescaped trailing space) rather than `evil\ ` (escaped).
4. Run `git check-ignore -v evil` (no trailing space) against the repo — Git reports it as ignored by that rule, while `git check-ignore -v "evil "` (the actual intended file) is not matched the same way, demonstrating the semantic drift caused by the missing escape.

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

**File:** app/src/ui/changes/sidebar.tsx (L269-271)
```typescript
  private onIgnoreFile = (file: string | string[]) => {
    this.props.dispatcher.appendIgnoreFile(this.props.repository, file)
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
