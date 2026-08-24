## Title
Cloning a malicious repository executes attacker-controlled code via disabled Git clone-protection during recursive submodule checkout - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment used for `git clone --recursive`. This environment variable is the kill-switch for the hardening Git introduced (in the GHSA/CVE-2024-32002 class of fixes) that guards against malicious repositories using crafted nested/symlinked `.git` directories in submodules to write files outside the intended working tree during a recursive clone, potentially leading to hook execution / arbitrary code execution. By forcing this protection off for every clone Desktop performs, an attacker who controls the content of a repository (and its submodules) that a victim clones through Desktop can defeat a defense-in-depth guard that upstream Git ships enabled by default.

### Finding Description
`clone()` in [1](#0-0)  builds the environment for every `git clone --recursive` invocation and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, then always requests `--recursive` submodule initialization. This means Desktop never benefits from Git's clone protection env-var-based mitigation, regardless of what the remote repository contains.

The attacker primitive here is "attacker controls a cloned/fetched repository" — precisely the allowed threat model. A malicious repository can define a `.gitmodules` file whose submodule paths/urls are crafted (e.g. via nested symlinks, case-folding tricks on case-insensitive filesystems, or `.git` file redirection tricks that Git's clone-protection mechanism was specifically designed to reject) to make `git clone --recursive` write into or execute content outside the intended repository directory. With protection intentionally disabled, whatever mitigation Git added server-side for this class of bug is bypassed by Desktop's own configuration.

Existing guards in the same file — `isClonePathSensitive()` at [2](#0-1)  — only validate the top-level destination directory chosen by the user (e.g., not `~/.ssh`, `~/.gnupg`). They do nothing to constrain what a malicious `.gitmodules`/submodule tree can do once cloning proceeds, and they do not compensate for having disabled Git's own submodule-clone protection.

### Impact Explanation
If Git's protection mechanism this variable disables is meant to stop path/symlink-based writes or hook execution originating from a malicious submodule tree during `--recursive` clone, then explicitly turning it off for every single clone Desktop performs means any user who clones (or is redirected to clone via the `x-github-client://openRepo/...` deep link handled in [3](#0-2) , or via "Clone repository" from a URL) an attacker-supplied repository is exposed to that vulnerability class with no mitigation from the app. This can result in file writes outside the intended clone directory and, depending on the specific Git-side bug the flag guards against, code execution via hooks, which meets the "code execution / file write outside the repo" bar in the reporting criteria.

### Likelihood Explanation
Likelihood is high for exposure (every clone triggers this code path unconditionally, including clones initiated by clicking a link, "Open in Desktop", or entering a URL/owner-repo in the clone dialog) but exploitation depends on the victim cloning a specific attacker-crafted malicious repository with submodules — a normal, expected Desktop workflow, not requiring local access, admin rights, or social engineering beyond "clone this repo," which is the exact intended use case of the affected feature.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in [4](#0-3)  so Desktop inherits Git's default (protection enabled) behavior for `git clone --recursive`. If there is a legitimate compatibility reason this flag was disabled (e.g., a specific benign repository layout that otherwise fails to clone), that should be handled narrowly (e.g., re-enable protection and only special-case the failure with a clear error/opt-in) rather than disabling the safeguard for all clones.

### Proof of Concept
Exact reproduction requires the specific submodule-path trick that Git's `GIT_CLONE_PROTECTION_ACTIVE` guard was built to reject; the source code available in the index does not include Git's own C implementation of that check, so I cannot fully demonstrate the resulting file-write/RCE primitive end-to-end from local files alone. What is verifiable from local code:
1. `clone()` always sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and always passes `--recursive`: [1](#0-0) .
2. This function is reachable from the "Open in Desktop" deep-link flow (`openRepositoryFromUrl` → `openOrCloneRepository`) driven by an attacker-supplied URL, as parsed in [5](#0-4)  and consumed in [6](#0-5) , as well as from the ordinary "Clone repository" UI flow for any URL/owner-repo the user enters.
3. No other guard in the codebase re-enables or replicates the protection this environment variable provides; `isClonePathSensitive()` only checks the destination directory chosen by the user, not the submodule content of the source repository.

**Caveat**: I could not locate the C-level implementation of the Git protection this variable disables within this repository's index (it lives in Git itself, not in Desktop's source), so the precise exploitation mechanics (which symlink/case-folding trick triggers the bypass) cannot be confirmed purely from local code. A Devin session with full repo/checkout access and an up-to-date Git binary would be needed to build a concrete crafted submodule PoC and confirm actual file write/execution outside the clone directory.

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

**File:** app/src/lib/git/clone.ts (L81-93)
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
```

**File:** app/src/lib/parse-app-url.ts (L66-125)
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
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1940-1951)
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
```
