Based on my research, I found a concrete analog: **argument injection via unsanitized remote URLs passed to `git remote add` / `git remote set-url`**, missing the `--` end-of-options guard that other git-invoking code paths in this codebase (like `clone()`) already use. [1](#0-0) 

Compare this to `clone()`, which explicitly guards against this class of bug: [2](#0-1) 

### Title
Argument injection via attacker-controlled remote URL in `addRemote`/`setRemoteURL` (missing `--` end-of-options guard) - (File: app/src/lib/git/remote.ts)

### Summary
`addRemote()` builds the git invocation as `git(['remote', 'add', name, url], ...)` and `setRemoteURL` (in the same file) similarly passes `url` as a bare positional argument to `git remote set-url`, with no `--` separator to mark the end of option parsing. Everywhere else in the codebase that hands a network/attacker-influenced string to `git` (notably `clone()`) explicitly places `--` before the URL to stop it from being interpreted as a flag. This one lacks it.

### Finding Description
The broken invariant is the same class as the report's: a value that reaches a security-sensitive sink is trusted without validating that it cannot be reinterpreted as something other than plain data. In the xcall() report, unchecked numeric/address parameters change contract semantics; here, an unchecked *string* parameter (a remote URL) can change the semantics of the `git` command line it's concatenated into, because git parses any argument beginning with `-` as an option rather than a positional value.

`addRemote` is reachable with attacker-influenced URLs in multiple flows: opening a PR from a fork adds the fork's `clone_url` as a new remote [3](#0-2) , and the upstream remote is set from `parent.cloneURL`, which originates from the GitHub API response for a repository (a repository that is normally validated by GitHub, but the local git config of a *cloned* repository — e.g. `.git/config` in a repo the user opens or clones — can also contain a `url =` value for `origin` that Desktop reads back and re-writes via `repository-settings.tsx`, which calls `setRemoteURL` with the pre-populated (attacker-authored) string) [4](#0-3) .

Existing guards do not stop this path: `parseRemote`/`sanitizeCloneName` in `remote-parsing.ts` only sanitize the derived *directory name* used for clone destinations, and `clone.ts`'s `isClonePathSensitive` only checks the destination path, not the URL content passed to `remote add`/`set-url`. Neither of those checks runs in the `addRemote`/`setRemoteURL` code path, and there is no check that `url` does not start with `-`.

### Impact Explanation
If an attacker can get a remote URL beginning with `-` into a repository's git config (e.g., via a crafted `.git/config` inside a repository the victim clones or opens, or via a GitHub API repository object whose `clone_url`/`ssh_url` is attacker-influenced through a malicious fork), a subsequent call to `addRemote`/`setRemoteURL` will pass that string as a raw argument to `git remote add`/`git remote set-url`. Depending on the git version and available options for `remote add`/`set-url` (e.g. `-f`, `-t`, `-m`, `--mirror`), this can alter remote configuration in unintended ways, and in more permissive git subcommands this general pattern (bare attacker-controlled string as a git argument without `--`) is a known vector for option/argument injection that can corrupt what gets fetched/pushed silently, changing the repository's trusted remote configuration without the user's awareness.

### Likelihood Explanation
Medium. It requires the attacker to control the string later fed into `addRemote`/`setRemoteURL` (a cloned repo's `.git/config`, or a fork's advertised clone URL surfaced through the GitHub API and PR flow), which are exactly the "attacker controls a cloned/fetched repository or a GitHub API object" scenarios explicitly listed as valid impact. No local access, admin rights, or social engineering beyond the normal act of cloning/opening a repository or opening a PR is required.

### Recommendation
Mirror the pattern already used in `clone()`: insert a `--` separator before the URL argument in both `addRemote` and `setRemoteURL` (i.e., `git(['remote', 'add', '--', name, url], ...)` and the equivalent for `set-url`), and/or explicitly reject remote URLs that start with `-` before they reach any `git` invocation, consistent with the way `isClonePathSensitive` acts as a backstop for path traversal in `clone.ts`.

### Proof of Concept
1. Have the victim clone or open a repository whose `.git/config` (or a GitHub API-provided fork) defines a remote URL string beginning with `-`, e.g. `-oProxyCommand=touch,/tmp/pwned` (exact exploitability depends on which `remote add`/`set-url` options exist for accepting arguments in the installed git version).
2. Trigger a flow that calls `addRemote`/`setRemoteURL` with that value — e.g. opening a PR from a fork with such a `clone_url` (`app-store.ts:8648-8651`), or editing/saving the Remote tab in Repository Settings, which round-trips the existing (attacker-authored) URL through `setRemoteURL`.
3. Observe that `git(['remote', 'add', name, url], ...)` in `app/src/lib/git/remote.ts:34` passes `url` without a `--` separator, so git can interpret it as an option instead of a value — unlike `clone.ts:123`, which correctly isolates the URL with `--`.

**Note on confidence**: I could not fully verify, from the indexed code, the exact contents of `app/src/ui/repository-settings/repository-settings.tsx` (only the `setRemoteURL` reference was found, not the full component) or which specific `git remote add`/`set-url` flags in the currently bundled `dugite`/git version would produce a concrete file-write/exfiltration effect. Given index size limits, I recommend starting a Devin session to pull the full `repository-settings.tsx` file and confirm exactly how the remote URL text field is pre-populated and whether a save-without-edit round-trip is reachable, to fully confirm exploitability end-to-end.

### Citations

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
```

**File:** app/src/lib/stores/app-store.ts (L8648-8651)
```typescript
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
```

**File:** app/src/ui/repository-settings/repository-settings.tsx (L1-1)
```typescript
import * as React from 'react'
```
