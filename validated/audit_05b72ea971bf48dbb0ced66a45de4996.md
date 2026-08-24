### Title
Recursive clone explicitly disables Git's CVE-2024-32002 symlinked-submodule clone protection, enabling RCE from a malicious cloned repository - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` in `app/src/lib/git/clone.ts` invokes `git clone --recursive` with the environment variable `GIT_CLONE_PROTECTION_ACTIVE` hard-coded to `'false'`: [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE` is the internal flag Git itself uses to gate the hardening added for CVE-2024-32002 — a vulnerability where a maliciously crafted repository with nested submodules (exploiting case-insensitive/normalizing filesystems and symlinked `.git` directories) could get Git to write files or execute hooks outside the intended working tree during a recursive clone, leading to remote code execution. Git's fix makes the top-level `clone` set this variable to `true` and re-check it when spawning submodule clones so that the symlink/alias checks run consistently through the whole recursive-clone tree. By forcing it to `'false'` in the environment passed to every `git clone` invocation, Desktop unconditionally disables that protection for every clone operation, including `--recursive` clones of untrusted repositories.

### Finding Description
The broken invariant, reduced from the Bracket report's bug class ("attacker-controlled input defeats a safety check baked into an old/underlying library, permanently bricking the intended protection for everyone"), is: Desktop overrides a security-relevant environment flag that the underlying Git binary relies on to decide whether to run its own anti-exploitation checks. Just as `Bracket`'s `execute()` disabled the practical safety of `safeApprove` by feeding it attacker-influenced `amountIn`, Desktop's `clone()` disables Git's own submodule/symlink protection by feeding it a static `'false'` value for `GIT_CLONE_PROTECTION_ACTIVE`, regardless of the source of the repository being cloned.

- The clone path is directly reachable with attacker-controlled data: the `url` argument comes from user-typed URLs, CLI `--cli-clone` arguments, deep links (`x-github-client://openRepo/...` handled through `parseAppURL`/`dispatchURLAction`), and "Clone" buttons on repositories returned by the GitHub API — all cases where the remote content of the repository being cloned is not controlled by GitHub Desktop or the user, only chosen by them. [2](#0-1) [3](#0-2) [4](#0-3) 
- Every one of these paths eventually funnels into `clone()`, which always runs `git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'` and no way for the caller to opt back into protection: [5](#0-4) 
- Other hardening added to this exact function (`isClonePathSensitive`, path traversal defenses in `sanitizeCloneName`) shows the maintainers are aware of and defending against malicious-repository/URL threats in this code path, but none of those guards touch the submodule/symlink clone-time protection that Git itself implements — they only validate the destination path string, not the content Git writes once cloning begins. [6](#0-5) 
- There is no other layer in the codebase that re-implements or substitutes for the disabled Git check (no submodule path sanitization, no symlink rejection during recursive clone was found in `app/src/lib/git`), so disabling `GIT_CLONE_PROTECTION_ACTIVE` removes the only defense against this class of attack for every recursive clone Desktop performs.

### Impact Explanation
If exploitable against the user's installed Git version (i.e., a Git version affected by CVE-2024-32002 or a similarly gated future clone-time protection that also honors `GIT_CLONE_PROTECTION_ACTIVE`), an attacker who controls the content of a repository (public GitHub repo, malicious fork, or repo behind a compromised/malicious remote) can get a victim to clone it through Desktop's normal "Clone repository" UI, the CLI (`github clone owner/evil-repo`), or a crafted deep link, and achieve file writes outside the intended clone directory (e.g., into `.git/hooks` of the outer repo or elsewhere on disk) purely from the cloned content, potentially leading to code execution the next time Git or Desktop touches the corrupted `.git` directory. This satisfies the "attacker controls a cloned/fetched repository ... resulting in code execution / file write outside the repo" criterion.

### Likelihood Explanation
Likelihood is proportional to whether the user's bundled/embedded Git binary is affected by the vulnerability class this flag guards. Desktop ships its own Git binary (`dugite`), so the actual exploitability depends on whether that bundled Git version still needs `GIT_CLONE_PROTECTION_ACTIVE=true` to be safe. This code, however, unconditionally forces the flag off, which is a policy bug: it deliberately turns off a security control regardless of Git version, meaning any current or future Git-side protection gated by this exact variable is neutralized for all Desktop users, with no user interaction beyond a normal "clone this repository" action (no elevated privileges, no local access, no leaked credentials required).

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Either omit it entirely (let Git use its own default, protective behavior) or explicitly set it to `'true'`. If it was disabled to work around an unrelated Desktop issue (e.g., progress reporting or `--recursive` submodule flow), that interaction needs to be root-caused and fixed without disabling the anti-exploitation check — e.g., by upgrading the bundled Git to a version where the interaction is fixed, or scoping the disablement only to trusted, first-party clone sources (never to arbitrary/untrusted `url` values passed into `clone()`).

### Proof of Concept
1. An attacker publishes a repository containing nested submodules crafted per the CVE-2024-32002 technique (symlinked `.git`/casing-alias directory combined with a submodule that resolves into the outer `.git` directory on clone).
2. Victim clicks "Clone repository" in GitHub Desktop (or a `x-github-client://openRepo/<attacker-url>` deep link, or runs `github clone attacker/evil-repo` via the CLI) and selects the attacker's URL.
3. Desktop calls `clone(url, path, options)` in `app/src/lib/git/clone.ts`, which spawns `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment. [7](#0-6) 
4. Because the protection flag is off, Git's submodule/symlink safety check (normally engaged via this variable) does not run, allowing the crafted submodule content to be written outside the intended working directory during the recursive clone, per the CVE-2024-32002 primitive — assuming the bundled Git binary is subject to that class of check.

Note: I could not verify from the available code/history *why* this flag was set to `'false'` (repository history shows only a single squashed "Initial commit" for this file, so intent/commit rationale is unavailable), and full confirmation of exploitability additionally requires checking the exact bundled `dugite`/Git version's behavior with this flag, which is outside what the indexed code can show.

### Citations

**File:** app/src/lib/git/clone.ts (L10-47)
```typescript
/**
 * Check whether a resolved clone path targets a sensitive location that
 * should never be used as a clone destination. This is a backstop against
 * path traversal attacks where a crafted URL tricks the UI into deriving
 * a clone path outside the intended base directory.
 */
function isClonePathSensitive(unresolvedClonePath: string): boolean {
  const clonePath = Path.resolve(unresolvedClonePath).toLowerCase()
  const home = Path.resolve(homedir()).toLowerCase()

  if (clonePath === home) {
    return true
  }

  const sensitiveLocations = [
    Path.join(home, '.ssh'),
    Path.join(home, '.gnupg'),
    Path.join(home, '.config'),
    Path.join(home, '.config', 'git'),
    Path.join(home, '.gitconfig'),
  ]

  if (__WIN32__) {
    const appData = process.env.APPDATA
    if (appData) {
      sensitiveLocations.push(appData.toLowerCase())
      sensitiveLocations.push(Path.join(appData, 'gnupg').toLowerCase())
    }
  }

  for (const sensitive of sensitiveLocations) {
    if (clonePath === sensitive || clonePath.startsWith(sensitive + Path.sep)) {
      return true
    }
  }

  return false
}
```

**File:** app/src/lib/git/clone.ts (L81-125)
```typescript
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

  let opts: IGitStringExecutionOptions = { env }

  if (progressCallback) {
    args.push('--progress')

    const title = `Cloning into ${path}`
    const kind = 'clone'

    opts = await executionOptionsWithProgress(
      { ...opts, trackLFSProgress: true },
      new CloneProgressParser(),
      progress => {
        const description =
          progress.kind === 'progress' ? progress.details.text : progress.text
        const value = progress.percent

        progressCallback({ kind, title, description, value })
      }
    )

    // Initial progress
    progressCallback({ kind, title, value: 0 })
  }

  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1975-1996)
```typescript
  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
  }
```

**File:** app/src/main-process/main.ts (L282-291)
```typescript
  if (typeof args['cli-open'] === 'string') {
    handleCLIAction({ kind: 'open-repository', path: args['cli-open'] })
  } else if (typeof args['cli-clone'] === 'string') {
    handleCLIAction({
      kind: 'clone-url',
      url: args['cli-clone'],
      branch:
        typeof args['cli-branch'] === 'string' ? args['cli-branch'] : undefined,
    })
  }
```
