Based on the code I was able to retrieve, the strongest local analog to the report's "insufficiently strict escaping function" pattern is `escapeGitSpecialCharacters` in `app/src/lib/git/gitignore.ts`, which is structurally the same bug class as `escapeUnsafeCharacters`: a purpose-built sanitizer that only strips a hand-picked subset of characters and misses the one character that actually matters for breaking out of the target syntax (there, backslash-based JS string escaping; here, a bare newline that breaks out of a single `.gitignore` line).

I was unable to fully confirm the exact call chain from the UI action to `appendIgnoreFile` (tool calls to `app-store.ts` / `dispatcher.ts` failed to return content in my final iteration), so the following should be treated as based on what I could verify plus one unverified assumption, clearly flagged below.

### Title
Insufficient gitignore pattern sanitization allows gitignore rule injection via crafted file names - (File: app/src/lib/git/gitignore.ts)

### Summary
`escapeGitSpecialCharacters` only escapes the glob-wildcard characters `[ ] ! * # ?`, but does not strip or escape newline (`\n`/`\r`) characters before the string is concatenated into the repository's `.gitignore` file.

### Finding Description
`escapeGitSpecialCharacters` is defined as: [1](#0-0) 

It is used by `appendIgnoreFile` to sanitize a file path before it is turned into a `.gitignore` pattern and passed to `appendIgnoreRule`: [2](#0-1) 

`appendIgnoreRule` simply joins the (supposedly-escaped) patterns with `\n` and appends them to the existing `.gitignore` contents: [3](#0-2) 

Because the escape function's regex (`/[\[\]!\*\#\?]/g`) does not match `\n`/`\r`, if the input `filePath` itself already contains an embedded newline, that newline survives escaping unchanged. When the resulting string is written into `.gitignore`, the embedded newline is interpreted by Git as a line break — effectively letting the "escaped" single path add a second, attacker-chosen gitignore rule (e.g. a broad `*` or a negation `!secret-file`) that was never intended by the user who merely clicked "Ignore file" on one specific path.

### Impact Explanation
File names in a Git tree object can contain arbitrary bytes other than `NUL` and `/`, including newlines — such a name can be crafted into a cloned/fetched repository by an attacker with no special privileges over the victim. If GitHub Desktop's "Ignore file (or extension)" convenience action is invoked by the user on such an untracked path, the escaping function fails to neutralize the embedded newline, and an unintended, attacker-controlled `.gitignore` rule is silently injected. This can hide other files from `git status`/the Changes list (so they are silently excluded from what the user commits) or un-ignore previously ignored files, i.e. silent corruption of what the user commits, matching the report's underlying bug class (an escape function that is "not properly defined" and fails on characters outside its narrow allow/deny list).

### Likelihood Explanation
The path requires the attacker to control a filename inside a repository the victim has cloned/fetched and for the victim to take the normal, expected action of ignoring that specific untracked file through Desktop's UI — no unnatural steps, no elevated privileges. This aligns with the "attacker controls a cloned/fetched repository" trigger accepted as valid impact.

### Recommendation
- Update `escapeGitSpecialCharacters` (or add a companion sanitizer) to also strip or reject `\n`/`\r` characters before a path is turned into a gitignore pattern, and/or reject/`--`-quote the entire pattern rather than character-by-character escaping.
- Add a regression test (parallel to the existing `escapes string with special git characters` test in `app/test/unit/git/gitignore-test.ts`) that asserts a filename containing `\n` cannot introduce a second line into the resulting `.gitignore` content.

### Proof of Concept
1. Attacker publishes a repository containing an untracked file whose Git tree entry name is `legit.txt\n*`.
2. Victim clones/fetches the repository in GitHub Desktop and sees `legit.txt` (with embedded newline) in the Changes list as untracked.
3. Victim right-clicks and selects "Ignore file", which calls `appendIgnoreFile` → `escapeGitSpecialCharacters(filePath)`.
4. Because the sanitizer only escapes `[ ] ! * # ?` and not `\n`, the resulting pattern written to `.gitignore` becomes two lines: `legit.txt` and `*`.
5. The injected `*` line causes the entire working directory to be ignored going forward, silently corrupting what subsequent `git add`/commits/pushes will include, without the user ever intending or seeing that second rule.

Note: I could not verify within this session the exact UI call path from `appendIgnoreFile` back through `app-store.ts`/`dispatcher.ts` to the "Ignore file" context-menu action (tool calls failed to return in the final iteration), nor did I verify empirically that Desktop's file-name display/parsing preserves a raw embedded newline all the way to the sanitizer input rather than normalizing/escaping it earlier in the pipeline. If Desktop already quotes or filters newline-containing paths upstream, this specific PoC step 2–3 would be blocked and the finding would need re-scoping.

### Citations

**File:** app/src/lib/git/gitignore.ts (L63-99)
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
