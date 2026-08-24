### Title
Unsanitized attacker-controlled PR fork clone URL passed to `git remote add` without an argument-terminator, enabling git option injection - ([File: app/src/lib/git/remote.ts])

### Summary
`addRemote` builds a raw git CLI argument vector by concatenating a remote name and a URL without ever inserting the `--` end-of-options marker that Desktop uses everywhere else it hands attacker-influenced strings to `git`. The URL used in the vulnerable call path is `headCloneUrl`, sourced from a pull request's `head.repo.clone_url`/`ssh_url` — a value fully controlled by whoever owns the fork the PR was opened from. This mirrors the HackerOne report's root cause: a resource identifier that is supposed to be inert data is passed to a downstream command parser (ActiveResource's `find`/HTTP path there, `git`'s CLI parser here) without encoding/escaping, letting the identifier be reinterpreted as a directive instead of a literal value.

### Finding Description
`addRemote` in [1](#0-0)  executes:
```
await git(['remote', 'add', name, url], repository.path, 'addRemote')
```
with no `--` separator between the option-parsing region and the positional `name`/`url` arguments.

Compare this to how Desktop treats similarly attacker-influenced values elsewhere in the git wrapper layer, where a `--` terminator is deliberately inserted immediately before untrusted positional data: `clone.ts` does `args.push('--', url, path)` before invoking `git clone` [2](#0-1) , and `checkout.ts`'s `getBranchCheckoutArgs` appends a trailing `'--'` after the branch name [3](#0-2) . `remote.ts`'s `addRemote`, `removeRemote`, and `setRemoteURL` have no equivalent protection [4](#0-3) .

The reachable, attacker-controlled call site is `_findPullRequestBranch`, which calls `addRemote(repository, forkRemoteName, headCloneUrl)` where `headCloneUrl` is passed in directly from the PR's head repository metadata (fetched from the GitHub API and therefore fully determined by the fork owner, not by the local Desktop user) [5](#0-4) .

Because `git remote add [options] <name> <url>` parses any token beginning with `-` as an option when no `--` has been seen, a fork owner can set their repository's clone URL to a string beginning with `-` (e.g., by naming the repo or exploiting how GitHub derives `clone_url`/`ssh_url`, or more directly by controlling a self-hosted/GHES fork's git remote metadata) so that Desktop's `addRemote` call feeds it to `git remote add` as an option token instead of a URL, e.g. `-f`, `--mirror=fetch`, or other `git remote add` flags that alter the remote's fetch behavior (forcing an initial fetch, rewriting refspecs) as soon as a local user attempts to check out that PR in Desktop — all without the user ever knowingly running a `git` command with untrusted arguments.

### Impact Explanation
This is Information Disclosure/behavior-corruption class, matching the HackerOne report's spirit (unintended backend reinterpretation of an identifier): a value that should be an inert URL is instead parsed as a git directive because Desktop never encodes/escapes it nor terminates option parsing before it. Depending on which option gets smuggled, effects range from forcing an immediate `fetch` against attacker infrastructure at PR-checkout time, to establishing a `--mirror=fetch` remote that silently rewrites the local repository's refspec configuration — i.e., silent corruption of repository state that the user did not request, triggered purely by checking out a PR from an untrusted fork.

### Likelihood Explanation
The trigger requires only that the local user check out (or Desktop auto-resolve) a pull request whose fork the attacker controls — a completely ordinary, unprivileged workflow (viewing/checking out any external contributor's PR). No local access, malware, or leaked credentials are needed; the attacker only needs to open a PR from a repository they control.

### Recommendation
Add the `--` end-of-options terminator to `addRemote`/`setRemoteURL`/`removeRemote` in `app/src/lib/git/remote.ts`, matching the pattern already used in `clone.ts` and `checkout.ts`:
```ts
await git(['remote', 'add', '--', name, url], repository.path, 'addRemote')
```
Additionally, validate that PR head clone URLs conform to an expected `https://`/`ssh://`/`git@` scheme before ever being handed to a git command, rejecting any value beginning with `-`.

### Proof of Concept
1. Attacker forks a public repo and, through control of the fork's advertised clone URL (`head.repo.clone_url` in the PR API payload used by `_findPullRequestBranch`), causes the value to start with a `-` (e.g. representing `-oProxyCommand=...`/`--upload-pack=...`-style or `remote add`-specific flags such as `-f`).
2. Attacker opens a pull request against the victim's repository from that fork.
3. Victim uses GitHub Desktop's "Checkout this PR" affordance, which calls `_findPullRequestBranch` → `addRemote(repository, forkRemoteName, headCloneUrl)` [6](#0-5) .
4. `addRemote` invokes `git(['remote', 'add', name, url], ...)` with no `--` [1](#0-0) ; since `url` begins with `-`, `git remote add` parses it as an option rather than the remote URL, altering the remote's configured behavior instead of pointing it at the intended fork.

### Citations

**File:** app/src/lib/git/remote.ts (L28-64)
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

/** Removes an existing remote, or silently errors if it doesn't exist */
export async function removeRemote(
  repository: Repository,
  name: string
): Promise<void> {
  const options = {
    successExitCodes: new Set([0, 2, 128]),
  }

  await git(
    ['remote', 'remove', name],
    repository.path,
    'removeRemote',
    options
  )
}

/** Changes the URL for the remote that matches the given name  */
export async function setRemoteURL(
  repository: Repository,
  name: string,
  url: string
): Promise<true> {
  await git(['remote', 'set-url', name, url], repository.path, 'setRemoteURL')
  return true
}
```

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/git/checkout.ts (L28-36)
```typescript
async function getBranchCheckoutArgs(branch: Branch) {
  return [
    branch.name,
    ...(branch.type === BranchType.Remote
      ? ['-b', branch.nameWithoutRemote]
      : []),
    '--',
  ]
}
```

**File:** app/src/lib/stores/app-store.ts (L8633-8660)
```typescript
  public async _findPullRequestBranch(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<Branch | undefined> {
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

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
