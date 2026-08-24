## Finding: Confirmed — newline is not escaped, allowing gitignore line injection

### Summary
`escapeGitSpecialCharacters` is applied independently to each array element in `appendIgnoreFile` [1](#0-0) , but its regex only escapes `[ ] ! * # ?` and does **not** escape or strip newline characters (`\n`/`\r`) [2](#0-1) . Since `appendIgnoreRule` later joins the (already-escaped) elements with `'\n'` [3](#0-2) , a single array element whose value already contains an embedded newline is functionally indistinguishable from two separate lines once written to `.gitignore`. So while the mapping over the array is done correctly (no cross-element contamination), one crafted filename can still smuggle in an extra, attacker-chosen line.

### Finding Description
`appendIgnoreFile` is meant to add exactly one ignore pattern per selected file/path. Because the escaping function's character class doesn't include `\n`/`\r`, a repository file whose name contains an embedded newline (legal on POSIX filesystems, since only `/` and NUL are forbidden) passes through untouched aside from character-class substitutions elsewhere in the string. When such a path reaches `appendIgnoreRule` → `formatGitIgnoreContents` → `saveGitIgnore`, the embedded `\n` is written verbatim into `.gitignore`, effectively creating a new, attacker-controlled ignore-rule line unrelated to the file the user actually selected [4](#0-3) .

One mitigating detail: because `escapeGitSpecialCharacters` runs over the *entire* string (not just leading characters), any `!` the attacker places after the injected newline (to attempt a negation/"un-ignore" rule) is also escaped to `\!`, converting it to a literal-ignore pattern for a filename starting with `!` rather than a real negation rule. This blocks the "unignore an existing pattern" variant of the attack, but does not block injection of an arbitrary additional *ignore* pattern (e.g. targeting future files, hooks configs, CI files, etc., depending on quoting/globbing tricks with `*`/`?`, which also get escaped, further limiting glob-based tricks).

### Impact Explanation
This allows a malicious repository to cause GitHub Desktop to silently write an unintended `.gitignore` rule when the victim uses the "Ignore file"/"Ignore files" action on a crafted path from that repo's Changes list. That fits the "silent corruption of what the user commits or pushes" impact category, since it can cause legitimate files to stop being tracked without the user's awareness. The negation/unignore variant (which would be more severe, e.g. resurrecting a previously-ignored secrets file) appears to be neutralized by the escaping of `!` throughout the string.

### Likelihood Explanation
Requires: (1) an attacker-controlled repository containing a file/path with an embedded newline character, and (2) the victim explicitly invoking "Ignore file"/"Ignore files" on that entry via the Changes sidebar. This is a plausible but non-default user action, and cross-platform behavior of newline-containing filenames (Windows checkout behavior in particular) was not verified within available tooling.

### Recommendation
Update `escapeGitSpecialCharacters` (or add a pre-processing step in `appendIgnoreFile`) to also escape/strip `\r` and `\n` (and ideally reject/sanitize any control characters) before the value is used as an ignore pattern, ensuring one selected file always maps to exactly one `.gitignore` line.

### Proof of Concept
1. In a repository, create a tracked file whose name contains a literal newline followed by attacker content, e.g. (conceptually) `legit.txt\n*.important`.
2. In GitHub Desktop, open the repository, select this file's Changes entry, and use "Ignore file".
3. Inspect `.gitignore`: it now contains an injected extra line (`*.important`) unrelated to the originally selected file, because the embedded `\n` was never escaped by `escapeGitSpecialCharacters`.

### Citations

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

**File:** app/src/lib/git/gitignore.ts (L90-96)
```typescript
  if (filePath instanceof Array) {
    const escapedFilePaths = filePath.map(path =>
      escapeGitSpecialCharacters(path)
    )

    return appendIgnoreRule(repository, escapedFilePaths)
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
