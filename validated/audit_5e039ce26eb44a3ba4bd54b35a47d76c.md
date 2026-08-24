## Finding

The strongest analog to "missing checks on immutable/critical address" I found in this codebase is not an unchecked address per se, but a git-internal safety flag that Desktop unconditionally disables during every recursive clone of an attacker-influenced URL. [1](#0-0) 

### Title
GitHub Desktop unconditionally disables Git's built-in clone protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) for every recursive clone of a remote-supplied URL - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` builds every clone invocation with `--recursive` and forces the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to the literal string `'false'` [2](#0-1) . This value is applied unconditionally to any `url` passed in, regardless of whether that URL originates from a user-typed string, a "Clone Repository" API selection, an `x-github-client://` deep link, or a fork's `clone_url` pulled from the GitHub API (e.g. via `_findPullRequestBranch` / `openOrCloneRepository`) [3](#0-2) .

### Finding Description
`GIT_CLONE_PROTECTION_ACTIVE` is a Git-internal guard flag (not a documented, user-facing configuration option) that modern Git versions set themselves while performing a recursive clone in order to detect and block malicious submodule layouts — e.g. a submodule whose `.git` entry is crafted (via symlinks, case-confusable paths, or NTFS/HFS+ path quirks) so that Git ends up writing files into the *containing* repository's real `.git/hooks` directory instead of the isolated submodule directory. That class of bug (fixed upstream as CVE-2024-32002/32004/32020/32021/32465) allows a cloned repository to plant an executable hook (e.g. `post-checkout`) that Git then runs automatically, giving the attacker code execution the moment the victim clones or checks out the repository.

By setting `GIT_CLONE_PROTECTION_ACTIVE=false` on every `clone()` call, Desktop is deliberately turning that internal protection off for the one operation (`git clone --recursive`) where it matters most, and it does so for every clone regardless of the source or trustworthiness of `url`. Nothing in `clone()` validates that the target repository (or its submodules) is safe before disabling the guard — the "address" here is the untrusted git remote/URL, and the "missing check" is the removal of Git's own built-in validation of the repository/submodule layout it is about to write to disk.

### Impact Explanation
If an attacker publishes (or forks) a repository containing a submodule engineered to defeat path/case checks on the victim's filesystem, and a Desktop user clones that repository (directly, via "Open in Desktop" deep link, or via a PR fork remote flow), Desktop's forced `GIT_CLONE_PROTECTION_ACTIVE=false` removes the safety net Git itself would otherwise apply, potentially letting the attacker write a hook file outside the intended submodule sandbox that Git subsequently executes — i.e., code execution outside the intended repo boundary, triggered purely by cloning a hostile repository. This matches the "attacker controls a cloned/fetched repository ... resulting in code execution ... outside the repo" criterion.

### Likelihood Explanation
Likelihood depends on which Git binary Desktop bundles/uses and whether that Git version still enforces protections that this flag would otherwise gate; I could not verify the exact Git version pinned by this repo or find in-repo documentation/tests explaining *why* this flag is forced to `false` (no comment accompanies line 83, and no reference to `GIT_CLONE_PROTECTION_ACTIVE` exists anywhere else in the codebase). It is also possible this was set to preserve legacy/expected error-handling behavior around Git's newer default clone protections and is intentional, in which case the real bug would be that it is applied indiscriminately rather than scoped away from untrusted remotes. This uncertainty should be resolved by checking the bundled Git/dugite version and any upstream changelog entries referencing this variable before treating it as confirmed-exploitable.

### Recommendation
- Do not force `GIT_CLONE_PROTECTION_ACTIVE=false` unconditionally; only disable it if there is a proven, version-specific compatibility need, and scope that exception narrowly with a clear comment/test explaining the tradeoff.
- Verify the bundled Git version's default protections for recursive submodule clones and ensure Desktop does not regress fixes for CVE-2024-32002 and related submodule/hook-write CVEs.
- Add explicit tests cloning a crafted repository with a maliciously-cased/symlinked submodule to confirm Desktop rejects or safely handles it rather than writing outside the clone destination.

### Proof of Concept
1. Attacker publishes a repository containing a submodule entry crafted to exploit case-insensitive or symlink-based path confusion in `.git/modules` (the general shape of the upstream Git advisories this protection flag exists for).
2. Victim uses GitHub Desktop's "Clone repository" (or an "Open in Desktop" link, or checks out a malicious fork via a PR) pointing at the attacker's repository/URL.
3. Desktop calls `clone(url, path, options)`, which runs `git -c ... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` forced in the environment [4](#0-3) .
4. Because Git's internal clone-protection check is disabled, the crafted submodule can write a hook file into the parent repository's real hooks directory during the recursive clone.
5. The planted hook subsequently executes on a normal follow-up Git operation performed by Desktop or the user (e.g. checkout/commit), achieving code execution outside the intended repository sandbox.

Note: I was unable to confirm from the indexed code alone which exact Git/dugite version ships with this build or whether this flag's effect has already been neutralized by a newer default; a Devin session with terminal access to run `git --version`/`dugite` version checks and to reproduce the PoC end-to-end would be needed to fully confirm exploitability.

### Citations

**File:** app/src/lib/git/clone.ts (L68-93)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }

  const defaultBranch = options.defaultBranch ?? (await getDefaultBranch())

  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/lib/stores/app-store.ts (L8633-8651)
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
```
