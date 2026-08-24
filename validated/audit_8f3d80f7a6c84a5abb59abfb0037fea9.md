## Analysis

The external report's core pattern is: **a security precondition exists elsewhere in the system, but the code path that matters explicitly bypasses/disables it**, causing the invariant (only vetted actors may perform sensitive actions) to be silently violated.

I looked for a Desktop analog where a git operation on attacker-controlled repository content explicitly disables a git-provided safety check, since that maps directly onto "bypassing a required verification before allowing an action that assumes the check was performed."

That is exactly what happens in the clone implementation.

### Title
Recursive clone explicitly disables Git's clone-time repository confusion protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` runs `git clone --recursive` while setting the environment variable `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, unconditionally, for every clone triggered by a user (including clones initiated via the "Open in Desktop" deep link / `x-github-client://openRepo/...` handler and CLI `--cli-clone`). This variable is Git's own kill-switch for clone-time repository-confusion protections that were added upstream to defend against symlink/case-collision attacks in nested/submodule `.git` directories (the class of issue behind CVE-2024-32004 and related advisories). By forcing it to `'false'`, Desktop deliberately turns off a safeguard that Git itself assumes is active for any clone of an untrusted remote. [1](#0-0) 

### Finding Description
- `clone()` builds the environment for the `git clone` subprocess and always sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, then adds `--recursive` unconditionally. [1](#0-0) 
- `--recursive` causes Git to automatically initialize and clone every submodule referenced by the top-level repository, running submodule hooks/checkout logic as part of the clone — this is precisely the surface the upstream clone-protection mechanism was built to guard (malicious repositories that plant executable content into a nested `.git` via symlinks/case-insensitive filesystem tricks during recursive clone/checkout).
- The clone path itself is reachable purely by the user clicking an attacker-supplied link: `parseAppURL` parses `openRepo` deep links into an `IOpenRepositoryFromURLAction` with a `url` field taken directly from the link, with no host/scheme allow-listing beyond basic structural checks (branch/pr regex, no absolute filepath). [2](#0-1) 
- `Dispatcher.openRepositoryFromUrl` → `openOrCloneRepository` → `AppStore._clone` eventually funnels that attacker-controlled `url` straight into `git.clone()`; the destination path is only checked for a small deny-list of sensitive OS folders (`isClonePathSensitive`), not for the safety of the *content* being cloned. [3](#0-2) [4](#0-3) 
- Nothing in this path re-enables or conditionally gates `GIT_CLONE_PROTECTION_ACTIVE` based on trust level of the remote — it's disabled for every clone, always, regardless of whether the URL came from a manually typed GitHub.com HTTPS URL or an arbitrary deep link/CLI argument pointing at an attacker-hosted repository.

This is the direct analog of the report's "unrestricted gameplay without registration": Git's own protection mechanism assumes it is active before performing recursive clone/checkout of untrusted content, but Desktop unconditionally flips that assumption off before the operation runs.

### Impact Explanation
If the disabled protection is the mechanism guarding against known clone/checkout-time repository-confusion attacks in nested `.git`/submodule structures, an attacker who controls the cloned repository (hosted anywhere, reached via a crafted `x-github-client://openRepo/<attacker-url>` link, a CLI `github clone` argument, or a normal manual clone of a malicious repo) can plant content that executes during `git clone --recursive` on the victim's machine — i.e., code execution outside of any user-reviewed commit, achieved purely by the user opening a link or cloning a repository. This satisfies the "attacker controls a cloned/fetched repository ... resulting in code execution" impact class.

### Likelihood Explanation
Likelihood is high for exposure (every clone goes through this code path, including the fully attacker-triggerable deep-link flow), but the actual exploitability depends on the specific Git version bundled with Desktop and whether the underlying repository-confusion bug the flag guards against is still unpatched/reachable in that bundled Git. I could not verify the exact Git version bundled or whether the specific CVE this flag disables is otherwise mitigated by other bundled Git patches — that would require inspecting the vendored Git binaries/version manifest, which is outside what the index exposes.

### Recommendation
Do not unconditionally disable `GIT_CLONE_PROTECTION_ACTIVE`. If there is a compatibility reason to set it (e.g., avoiding false-positive protection failures on legitimate repos), gate it behind a narrower, justified condition and keep the protection enabled by default for all recursive clones of remote/untrusted URLs, especially those reachable via protocol-handler/CLI input.

### Proof of Concept
Conceptual PoC (exact reachability of the underlying Git-level exploit could not be fully verified from the index alone):
1. Attacker publishes a malicious Git repository containing a submodule structure crafted to exploit the clone/checkout repository-confusion class of bug (symlinked or case-colliding `.git` path in a submodule).
2. Attacker sends the victim a link: `x-github-client://openRepo/https://github.com/attacker/evil-repo`.
3. Victim (who has GitHub Desktop installed) clicks the link.
4. `parseAppURL` → `Dispatcher.dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository` → `AppStore._clone` invoke `git.clone()`. [5](#0-4) 
5. `git.clone()` runs `git clone --recursive ...` with `GIT_CLONE_PROTECTION_ACTIVE=false`, disabling Git's own defense against the crafted submodule content, potentially resulting in code execution during the clone/checkout on the victim's machine. [6](#0-5)

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

**File:** app/src/lib/parse-app-url.ts (L66-128)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

  return unknown
}
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1955)
```typescript
  private async openRepositoryFromUrl(action: IOpenRepositoryFromURLAction) {
    const { url, pr, branch, filepath } = action

    let repository: Repository | null

    if (pr !== null) {
      repository = await this.openPullRequestFromUrl(url, pr)
    } else if (branch !== null) {
      repository = await this.openBranchNameFromUrl(url, branch)
    } else {
      repository = await this.openOrCloneRepository(url)
    }

    if (repository === null) {
      return
    }
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
