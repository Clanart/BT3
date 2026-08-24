### Title
Argument injection into `git push` via attacker-controlled tag names lacking a `--` separator - (File: `app/src/lib/git/push.ts`)

### Summary
`push()` splices `tagsToPush` directly into the `git push` argv without a `--` end-of-options marker, and the tag names in that array are derived from parsing the remote's own dry-run push porcelain output, which in turn reflects ref names that were accepted into the local repository from a remote during fetch. Because GitHub Desktop's own ref-name sanitizer does not forbid a leading `-`/`+` for refs that arrive via fetch (that stripping only happens for tags created through the app's "create tag" UI flow), a remote can advertise a tag whose name begins with `-`, and that string ends up as a literal, unguarded argv element passed to `git push`.

### Finding Description
`push()` builds the argument vector like this: [1](#0-0) 

`tagsToPush` is produced by `fetchTagsToPush`, which runs `git push --dry-run --porcelain` and parses the `<from>:<to>` field of each new-tag line to extract `tagName`: [2](#0-1) 

That `tagName` is a real local ref name (`refs/tags/<name>`), stripped of the `refs/tags/` prefix, with no further validation. The app's own sanitizer (`sanitizedRefName` in `app/src/lib/sanitize-ref-name.ts`) strips a leading `-`/`+` only when it's applied — e.g., when a user types a tag name in the UI — but it does not forbid a leading dash in git's own `check-ref-format` rules: [3](#0-2) 

git's ref-name validation itself does not reject refnames beginning with `-`, so a tag fetched from a remote (e.g., `refs/tags/-o=/tmp/x` or `refs/tags/--upload-pack=touch /tmp/pwned;`) can be stored locally as a legitimate ref without ever passing through the app's UI-side sanitizer. Once such a tag is new relative to the remote, `fetchTagsToPush` will surface it as a "new tag" entry, and `push()` will append it verbatim to `args` with no preceding `--` separator, unlike several other git wrappers in this codebase (`checkout.ts`, `diff.ts`, `reset.ts`, `add.ts`, `clone.ts`, etc.) which do add a `--` before positional path/ref arguments to stop exactly this class of issue.

### Impact Explanation
Because the tag string is spawned as a literal process argument (no shell involved, so classic shell injection is not possible), the primary risk is argv-level flag injection into `git push`: a crafted "tag name" beginning with `-` is parsed by git as an option rather than a refspec/positional argument. Depending on which option string can be smuggled through a valid (but attacker-influenced) ref name, this could alter push behavior (e.g., unintended `--force`-like flags, altering the receive-pack/upload-pack invoked, or manipulating other push options), which affects what gets pushed/how, and can be built into supply-chain or repo-tampering scenarios once a user pushes tags fetched from a malicious/compromised remote.

### Likelihood Explanation
This requires the victim to (1) add or interact with an attacker-controlled/compromised remote, (2) fetch tags from it (or have tags auto-fetched), and (3) subsequently push with tags enabled. This is a plausible but not trivial user flow, and exploitation is further constrained by which git option flags can be embedded in a syntactically valid ref name and by git's own re-validation of refspecs it receives on the argv (some malformed refspecs will simply cause git to error out rather than silently misbehave). I was not able to fully verify, within the available tools, exactly which option strings are exploitable end-to-end (i.e., which git-push flags can be smuggled via a valid ref-name string and what damage they cause) — that would require constructing and running a proof-of-concept against a live `git push` invocation, which is outside the scope of static code reading here.

### Recommendation
In `app/src/lib/git/push.ts`, insert a `--` separator before spreading `tagsToPush` (and before the branch/remote refspec) so all subsequent tokens are unambiguously treated as positional refspecs by git, mirroring the pattern already used in `checkout.ts`, `diff.ts`, `reset.ts`, and other wrappers. Additionally, validate/reject ref names returned by `fetchTagsToPush` that start with `-` (or otherwise do not match an expected tag-name shape) before they are used as push arguments.

### Proof of Concept
Conceptual PoC (not executed):
1. Attacker controls a git remote and advertises a tag ref, e.g. `refs/tags/-o=core.gitProxy=/tmp/evil.sh`, during a `git fetch --tags` initiated by GitHub Desktop.
2. Because `sanitizedRefName`'s stripping of leading `-`/`+` is only applied in the app's own tag-creation UI path and not to refs learned via fetch, the ref is stored locally as `refs/tags/-o=core.gitProxy=/tmp/evil.sh`.
3. User pushes their branch with tags; `fetchTagsToPush` (`app/src/lib/git/tag.ts:113-127`) parses the dry-run porcelain output and extracts `tagName = "-o=core.gitProxy=/tmp/evil.sh"`.
4. `push()` (`app/src/lib/git/push.ts:63-65`) does `args.push(...tagsToPush)`, producing an argv such as `['push', 'origin', 'main:main', '-o=core.gitProxy=/tmp/evil.sh']`, which `spawnGit` passes to `dugite`/git as literal option-like tokens instead of a tag refspec.

I could not verify with the available static tools which specific git-push option string is both (a) constructible as a syntactically valid ref name and (b) has a meaningful, harmful effect when injected this way — a background agent with a live git environment would be needed to confirm the exact exploitable flag and its impact.

### Citations

**File:** app/src/lib/git/push.ts (L57-65)
```typescript
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]

  if (tagsToPush !== null) {
    args.push(...tagsToPush)
  }
```

**File:** app/src/lib/git/tag.ts (L118-128)
```typescript
  while (currentLine < lines.length && lines[currentLine] !== 'Done') {
    const line = lines[currentLine]
    const parts = line.split('\t')

    if (parts[0] === '*' && parts[2] === '[new tag]') {
      const [tagName] = parts[1].split(':')

      if (tagName !== undefined) {
        unpushedTags.push(tagName.replace(/^refs\/tags\//, ''))
      }
    }
```

**File:** app/src/lib/sanitize-ref-name.ts (L1-11)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}
```
