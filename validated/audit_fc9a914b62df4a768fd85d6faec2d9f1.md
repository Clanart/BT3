### Title
Argument injection via unsanitized fork clone URL in `addRemote` when checking out pull requests from a fork - ([File: app/src/lib/git/remote.ts])

### Summary
GitHub Desktop's pull-request checkout flow adds a git remote using the PR's fork `clone_url` taken directly from the GitHub API response, without validating that the URL is a well-formed `https://`/`ssh://` remote or rejecting values that could be mistaken for command-line flags by the underlying `git remote add` invocation. Unlike `clone()`, which explicitly appends `--` before the untrusted `url` argument, `addRemote()` does not use an argument-terminator, so a crafted fork "clone URL"-shaped value that begins with `-` would be positioned as an option to `git remote add` rather than as a plain positional URL.

### Finding Description
When a user opens/checks out a pull request from a fork, the dispatcher calls `_findPullRequestBranch`, which calls `addRemote(repository, forkRemoteName, headCloneUrl)` where `headCloneUrl` is `pullRequest.head.repo.clone_url` — a value that originates from the GitHub API object describing the PR's head repository: [1](#0-0) [2](#0-1) 

`addRemote` builds the git argv without an argument-terminator (`--`) before the user/API-controlled `url`: [3](#0-2) 

Compare this to `clone()` in the same codebase, which recognizes the same class of risk and defends against it by inserting `--` before the untrusted `url` and `path` arguments: [4](#0-3) 

Git subcommands parse `-`/`--` prefixed tokens as options regardless of position unless an explicit `--` separates positional arguments. Because `addRemote`'s argv is `['remote', 'add', name, url]` with no separator, a `url` value starting with `-` is not guaranteed to be treated as a literal remote URL by `git remote`. The `git()` invocation is exec'd via argv array (not a shell), so classic shell-metacharacter injection is not possible, but the missing `--` separator is a genuine gap relative to the pattern already used elsewhere in the same file (`clone.ts`).

### Impact Explanation
This is the closest available analog to the report's "missing rejection/validation leading to unexpected/unhandled state": rather than a fallback function rejecting unexpected Ether, this path lacks a guard to reject or neutralize a malformed/attacker-influenced `clone_url` before it is fed into a `git` subcommand. If the head repository's `clone_url` (attacker-controlled insofar as the PR/fork author or a malicious GitHub Enterprise API response could supply it) begins with `-`, the argument could be interpreted as a git option instead of a URL, potentially altering the behavior of `git remote add` (e.g., unexpected remote configuration) rather than the expected remote-add operation, silently corrupting the repository's git configuration. I could not fully verify a specific `git remote add` option that yields code execution — only that the missing `--` separator, when compared with the same repo's own `clone.ts` treatment, represents a broken invariant (untrusted URL should never be able to influence argv parsing as anything other than a value).

### Likelihood Explanation
The `clone_url` in this path is normally validated/populated by GitHub's API for real repositories, so exploitability depends on whether a hostile GitHub Enterprise Server, a compromised API response, or some other attacker-influenced source can supply an out-of-shape `clone_url` for a fork. This is a narrower attack surface than the fully-clickable "Open in Desktop" URL scheme, and Desktop does have other guards in this area (`resolveWithin`, `sanitizeCloneName`, `isClonePathSensitive`, `testForInvalidChars`) that don't cover this particular sink. Because the risk depends on control over API data rather than a directly user-clicked link, likelihood is lower/medium, and I was not able to fully confirm end-to-end exploitability (e.g., a concrete `git remote add` flag that causes file write/execution) within the available tooling.

### Recommendation
Mirror the defense already used in `clone()`: insert `--` before the untrusted `url` argument in `addRemote`, `setRemoteURL`, and any other `git remote ...` call sites that accept externally-sourced URLs, e.g. `['remote', 'add', '--', name, url]` (verified against git's actual option-parsing behavior for `remote add`), and additionally validate that `clone_url` matches an expected `https://`/`ssh://`/`git://` remote URL shape (as already done via `parseRemote`/`remote-parsing.ts`) before it is used to add a remote, rejecting and logging any value that does not conform — analogous to implementing a "reject unexpected input" fallback instead of silently accepting it.

### Proof of Concept
Not independently executable from static review alone. Conceptually:
1. Cause `pullRequest.head.repo.clone_url` to be a string beginning with `-` (e.g., via a compromised/spoofed GitHub Enterprise API response, since this field is normally sanitized by github.com itself).
2. Open/checkout that PR in Desktop, triggering `_findPullRequestBranch` → `addRemote(repository, forkRemoteName, headCloneUrl)`.
3. `git(['remote', 'add', name, headCloneUrl], ...)` is executed without a `--` separator, so `headCloneUrl` could be parsed as a git option rather than a literal remote URL.

I was unable to confirm a specific weaponizable `git remote add` flag within the scope of this review; this should be validated against the exact `git` version behavior before treating it as confirmed RCE-capable, but the missing `--` separator relative to `clone.ts`'s own established mitigation is a verifiable code-level gap.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8643-8660)
```typescript
    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2039-2048)
```typescript
    await this.appStore._checkoutPullRequest(
      repository,
      pullRequest.number,
      pullRequest.head.repo.owner.login,
      pullRequest.head.repo.clone_url,
      pullRequest.head.ref
    )

    return repository
  }
```

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
