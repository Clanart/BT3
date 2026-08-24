## Finding: Git's CVE-2024-32004 clone-time protection is force-disabled during recursive clones [1](#0-0) 

### Title
Recursive Clone Disables Git's Symlink/Hook Clone-Protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - (File: app/src/lib/git/clone.ts)

### Summary
The report's underlying pattern is: a value that comes from an attacker-influenced source (the Pyth price feed) is consumed by a critical operation without the safety check that exists specifically to prevent that value from being abused (a fee/guard). The Desktop analog is `clone()` in [2](#0-1)  which unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` for every clone that Desktop performs, including the default `--recursive` (submodule) clone of a completely untrusted, attacker-authored remote URL.

### Finding Description
`GIT_CLONE_PROTECTION_ACTIVE` is git's own internal safety flag, introduced as part of the fix for CVE-2024-32004 (and the related symlinked `.git`/hooks family of clone-time RCE issues). Git uses this flag to prevent a maliciously crafted repository/submodule tree from tricking a recursive clone into treating a symlinked path as the `.git` directory (or config) of a submodule, which could otherwise let a project's checked-out files rewrite `core.hooksPath`/hook scripts and have them executed during the clone itself — before the user ever "trusts" the code.

Desktop's `clone()` function takes an arbitrary, user- or link-supplied `url` (this is the exact same function invoked by `openOrCloneRepository`/`openBranchNameFromUrl`/`openPullRequestFromUrl` reachable from `x-github-client://openRepo/...` deep links, see [3](#0-2)  and [4](#0-3) ) and always builds:

```
env = { ...envForRemoteOperation(url), GIT_CLONE_PROTECTION_ACTIVE: 'false' }
args = ['-c', 'init.defaultBranch=...', 'clone', '--recursive', ..., url, path]
``` [1](#0-0) 

By explicitly forcing this variable to `'false'` for the top-level (and by inheritance, the nested submodule) clone invocations, Desktop deliberately turns off the exact guard that git added to stop a malicious remote/submodule tree from smuggling hook execution or config takeover through a recursive clone. The existing local mitigations in this file — `isClonePathSensitive()` (blocking clones into `~/.ssh`, `~/.gnupg`, etc.) and `sanitizeCloneName()`/`resolveWithin()` used elsewhere for path traversal — only address the *destination path* chosen by Desktop; none of them re-validate the *contents* of the cloned tree for the symlink/hooks abuse case that `GIT_CLONE_PROTECTION_ACTIVE` exists to stop. [5](#0-4) [6](#0-5) 

### Impact Explanation
An attacker who controls a repository (or a submodule referenced by that repository) that a victim clones through Desktop — via the normal "Clone a repository" UI, a `github-mac://openRepo/...`/`x-github-client://` deep link, or a pull request checkout that triggers a fetch/clone — can attempt to exploit the symlinked `.git`/hooks class of clone-time issues, because Desktop has explicitly opted the operation out of git's own protection. Depending on the underlying git version's remaining defenses, this could allow hook scripts embedded in the untrusted tree to execute on the victim's machine during the clone itself, i.e., before the user reviews or opens the repository — arbitrary code execution from a merely-cloned/fetched repository, which matches the "attacker controls a cloned/fetched repository ... resulting in code execution" impact category.

### Likelihood Explanation
This is not a timing/reordering race like the original report; it's a hard-coded, always-on disabling of a security control on every single clone Desktop performs (both explicit user clones and recursive submodule clones), so the "trigger condition" is simply: the victim clones or fetches a repository from an attacker. The comment/behavior gives no indication this was scoped only to the intended internal git use-case (temporarily disabling protection for the outer clone while re-enabling it for nested submodule clones, which is how `git submodule` uses this variable internally) — instead Desktop sets it once at the top and it propagates through the `--recursive` operation. I could not access commit history/PR context beyond the single squashed "Initial commit" in this repository, so I cannot confirm whether this was intentionally scoped to match git's own internal recursive-clone semantics or is a regression that blanket-disables the protection for the entire operation tree.

### Recommendation
- Do not statically force `GIT_CLONE_PROTECTION_ACTIVE=false`. Let git manage this flag internally during recursive/submodule clones (its own `--recursive`/`submodule update` machinery already toggles it correctly), and audit whether Desktop's use of `dugite`/its bundled git version already contains the CVE-2024-32004 fix before ever touching this variable.
- If there was a specific dugite/git compatibility reason for setting this flag, scope it as narrowly as documented by upstream git (i.e., only for the exact nested-submodule-clone step it is meant to cover), and add a regression test asserting that a submodule containing a symlinked `.git` path is rejected rather than followed.
- Add an explicit unit test (mirroring `clone-path-safety-test.ts`/`clone-test.ts`) that clones a fixture repository containing a malicious symlinked submodule `.git` entry and asserts the clone fails/hook is not executed.

### Proof of Concept
1. Attacker publishes a public repository containing a submodule whose `.git` reference is a symlink crafted to point at a path where a `core.hooksPath`/hook script can be written/read.
2. Victim clicks a `github-mac://openRepo/https://github.com/attacker/repo` link, opens a PR from that repo, or manually clones the URL in Desktop.
3. Desktop's `clone()` executes `git -c ... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'`, per [1](#0-0) , disabling git's own guard that would otherwise reject/neutralize the symlinked submodule during the recursive clone.
4. If the underlying git binary is otherwise vulnerable to the symlink/hooks clone issue this flag protects against, the crafted hook executes on the victim's machine during the clone step, before any explicit user trust decision.

Because I could not retrieve prior commit/PR history for this file (the repository only exposes a single squashed commit), I cannot fully confirm whether this is a newly-introduced regression versus a deliberate (but overly broad) compatibility shim; this should be verified against the actual bundled git/dugite version's behavior before treating this as confirmed-exploitable in the current build.

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

**File:** app/src/lib/remote-parsing.ts (L88-116)
```typescript
export function sanitizeCloneName(name: string): string | null {
  const components = name.split(/[/\\:]/)

  let lastComponent = ''
  for (let i = components.length - 1; i >= 0; i--) {
    if (components[i].length > 0) {
      lastComponent = components[i]
      break
    }
  }

  if (lastComponent.length === 0) {
    return null
  }

  if (lastComponent.endsWith('.git')) {
    lastComponent = lastComponent.slice(0, -4)
  }

  if (
    lastComponent === '..' ||
    lastComponent === '.' ||
    lastComponent.length === 0
  ) {
    return null
  }

  return lastComponent
}
```
