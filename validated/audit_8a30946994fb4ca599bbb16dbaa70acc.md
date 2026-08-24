Found a concrete, file-specific analog. The report's real broken invariant is: *a security control specifically designed to stop attacker-controlled content from executing unsafe actions is being deliberately bypassed, and the attacker-controlled input reaches that path without requiring anything beyond a normal user action (clicking a link / cloning a repo).* In GitHub Desktop, the closest match is not a reorg/race issue but a disabled Git anti-RCE safeguard on every clone operation.

### Title
Git's CVE-2024-32004 hook-execution protection is unconditionally disabled during recursive clone - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in [1](#0-0)  always runs `git clone --recursive` while explicitly setting `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the child process environment. This environment variable is Git's own internal mechanism (introduced to remediate CVE-2024-32004/CVE-2024-32465-class issues) that prevents a maliciously crafted repository — via recursively-populated submodules containing symlinks/hook files — from having its hooks executed *during the clone itself*, before the user has had any chance to inspect the content. By forcing this flag to `'false'` on every invocation, Desktop deliberately re-enables the exact unsafe behavior Git upstream closed.

### Finding Description
The `clone` function builds the environment for every clone Desktop performs: [2](#0-1) 

This code path is reachable with fully attacker-controlled input through multiple unprivileged surfaces:
- The manual "Clone repository" flow, where the URL is user/attacker supplied and dispatched to `dispatcher.clone` → `CloningRepositoriesStore.clone` → `cloneRepo` (this `clone`): [3](#0-2) 
- The "Open in Desktop" deep link (`x-github-client://openrepo/<url>`), parsed with no authentication of the target repository, and routed straight into the clone/open flow: [4](#0-3) [5](#0-4) [6](#0-5) 
- Opening a pull request from a URL, where the fork's `clone_url` comes directly from the GitHub API response for the PR's head repo (an attacker fully controls the content of their own fork) and is used to open/clone the repository: [7](#0-6) 

None of the existing guards address this: `isClonePathSensitive()` only validates the destination *path* on disk, not the *content* being cloned, and does nothing to restore Git's hook-execution protection: [8](#0-7) . Because `--recursive` is always passed, any submodules nested in the attacker's repository are also cloned in the same unprotected context: [9](#0-8) 

### Impact Explanation
If an attacker crafts a repository (potentially with recursively nested submodules) that exploits the class of clone-time hook/symlink issues, Desktop's forced `GIT_CLONE_PROTECTION_ACTIVE=false` removes Git's own defense-in-depth check meant to stop such content from running during the clone operation. This can result in code execution on the victim's machine driven purely by cloning attacker content — matching the "attacker controls a cloned/fetched repository … result is code execution" impact category. Unlike the original `create2`/reorg report (which is about address predictability + race), the shared invariant here is the same: a designed protection is disabled, letting attacker-supplied content produce an unintended, unsafe state change for the victim.

### Likelihood Explanation
`clone()` is the single, unconditional entry point used by every Desktop clone flow (manual clone dialog, "Open in Desktop" deep links, and PR-fork checkout), so the disabling of protection applies to 100% of clones, not a narrow edge case. Triggering it only requires the victim to clone or "Open in Desktop" a link/repo the attacker controls — actions the app actively facilitates and are within Desktop's normal, expected usage (no local access, admin rights, or social engineering beyond a normal repo/link click).

### Recommendation
- Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in [10](#0-9)  so Git's default (protection-active) behavior is preserved for all clone operations, including recursive submodule clones.
- If the override exists to work around legitimate errors thrown by the protection during trusted/local operations, scope the bypass narrowly (e.g., only for verified local-to-local test clones) instead of applying it globally to every network clone of untrusted, attacker-supplied URLs.
- Ensure the installed/bundled Git version actually implements this protection (i.e., is patched for the corresponding upstream CVE) rather than assuming it is safe to toggle.

### Proof of Concept
1. Attacker publishes a public repository (or a fork used for a PR) containing a submodule whose `.git`/hooks layout is crafted to exploit the clone-time hook-execution class of issues that `GIT_CLONE_PROTECTION_ACTIVE` is designed to block (recursively nested submodule with a hook path that resolves outside the intended submodule checkout via symlink).
2. Attacker sends the victim either (a) a normal repository URL, (b) an `x-github-client://openrepo/<attacker-repo-url>` deep link, or (c) a link to a pull request from the malicious fork.
3. Victim clones via Desktop's Clone dialog, clicks the deep link ("Open in Desktop"), or opens the PR in Desktop.
4. `clone()` runs `git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false` set: [2](#0-1) , executing the submodule clone(s) with Git's protective check disabled, allowing the crafted hook content to run as part of the clone.

### Citations

**File:** app/src/lib/git/clone.ts (L16-47)
```typescript
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

**File:** app/src/lib/stores/cloning-repositories-store.ts (L20-38)
```typescript
  public async clone(
    url: string,
    path: string,
    options: CloneOptions
  ): Promise<boolean> {
    const repository = new CloningRepository(path, url)
    this._repositories.push(repository)

    const title = `Cloning into ${path}`

    this.stateByID.set(repository.id, { kind: 'clone', title, value: 0 })
    this.emitUpdate()

    let success = true
    try {
      await cloneRepo(url, path, options, progress => {
        this.stateByID.set(repository.id, progress)
        this.emitUpdate()
      })
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1998-2048)
```typescript
  private async openPullRequestFromUrl(
    url: string,
    pr: string
  ): Promise<RepositoryWithGitHubRepository | null> {
    const pullRequest = await this.appStore.fetchPullRequest(url, pr)

    if (pullRequest === null) {
      return null
    }

    // Find the repository where the PR is created in Desktop.
    let repository: Repository | null =
      this.getRepositoryFromPullRequest(pullRequest)

    if (repository !== null) {
      await this.selectRepository(repository)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      log.warn(
        `Open Repository from URL failed, did not find or clone repository: ${url}`
      )
      return null
    }
    if (!isRepositoryWithGitHubRepository(repository)) {
      log.warn(
        `Received a non-GitHub repository when opening repository from URL: ${url}`
      )
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    if (pullRequest.head.repo === null) {
      return null
    }

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```
