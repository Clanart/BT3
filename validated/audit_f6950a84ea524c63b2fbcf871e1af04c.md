## Title
Git's clone symlink/reused-directory protection is explicitly disabled during recursive clones - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` in `app/src/lib/git/clone.ts` unconditionally clones with `--recursive` while forcing the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. [1](#0-0) 
This variable gates the upstream Git safety check (introduced for CVE‑2024‑32002) that refuses to clone repositories whose nested/submodule `.git` directories are symlinks or otherwise arranged to escape the intended working tree on case-insensitive or symlink-supporting filesystems, which upstream Git uses to prevent hooks from being written/executed outside the clone target. By setting `GIT_CLONE_PROTECTION_ACTIVE=false`, Desktop takes Git's own boolean protection check and forces it to always evaluate as "disabled," which is the same class of bug as `requirePolicyType()`: a safety check exists, produces a meaningful boolean, but the calling code deliberately prevents that boolean from being enforced.

### Finding Description
`clone()` builds its `git clone` invocation with the `--recursive` flag (so submodules are cloned automatically) and merges in an environment override:
```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
``` [2](#0-1) 
`GIT_CLONE_PROTECTION_ACTIVE` is the flag Git introduced specifically to allow this exact protection to be toggled; upstream Git defaults it to enabled after the CVE‑2024‑32002 fix, precisely because attacker-controlled repositories (and their submodules) can contain crafted paths (e.g. case-variant or symlinked `.git`/hooks directories) that, when cloned recursively on case-insensitive/symlink-capable filesystems (default on Windows and macOS), let a hook file be written into and executed from the real `.git/hooks` directory of the outer repository — leading to code execution as soon as the clone (or a subsequent checkout) runs.

Desktop's `isClonePathSensitive()` check, right above this code, only validates that the *destination path itself* isn't `~`, `~/.ssh`, `~/.gnupg`, etc. [3](#0-2)  — it does nothing to validate the *contents* of the remote repository being cloned, and does not compensate for the protection that has just been switched off. There is no other call site in `clone.ts` that re-enables or conditionally applies `GIT_CLONE_PROTECTION_ACTIVE`; it is hard-coded to `'false'` for every clone, including clones from unauthenticated/arbitrary remotes reached via the `x-github-client://openRepo/...` deep link (`parseAppURL` → `openRepositoryFromUrl` → `openOrCloneRepository` → clone dialog) [4](#0-3)  and via `IOpenRepositoryFromURLAction.url`, which is attacker-suppliable through a link the user clicks. [5](#0-4) 

### Impact Explanation
If Git's own protected-clone checks would otherwise reject a maliciously crafted repository (nested/symlinked `.git` in a submodule combined with `--recursive`), Desktop's explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override removes that backstop for every single clone operation performed by the app — including clones initiated by simply opening a link (`x-github-client://openRepo/<attacker-url>`) or clone-URL CLI action. This can lead to writing files (e.g. Git hooks) outside of the intended clone directory and subsequent code execution on the user's machine, which is squarely in-scope (attacker-controlled cloned repository → code execution / file write outside the repo).

### Likelihood Explanation
Likelihood is high for any Desktop user who clones an attacker-supplied repository URL, whether through the normal "Clone repository" dialog, the "Open in Desktop" website button (`x-github-client://openRepo/...`), or `--cli-clone`. No special privileges, local access, or social-engineering beyond "click a link/URL you were given" are required, and `--recursive` clones are the default for every clone Desktop performs, so the protective mechanism is disabled unconditionally rather than only in a hardened/opt-in path.

### Recommendation
Do not statically force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Rely on Git's default protected behavior (leave the variable unset/enabled) unless there is a narrowly-scoped, well-understood reason to bypass it, and if a bypass is genuinely required, ensure it is paired with equivalent application-level validation of the cloned/submodule tree structure (e.g., rejecting nested `.git` symlinks or path traversal in submodule names) before proceeding, mirroring how `requirePolicyType()` needed to actually act on `supportsInterface` instead of computing and discarding it.

### Proof of Concept
1. Attacker crafts a Git repository containing a submodule whose reported `.git` metadata is arranged to reference/overwrite a path outside the submodule root (the class of repository Git's CVE‑2024‑32002 protection is designed to reject on case-insensitive/symlink-aware filesystems).
2. Attacker distributes a link such as `x-github-client://openRepo/https://github.com/attacker/evil-repo` or otherwise gets the victim to clone the repository URL in Desktop.
3. Desktop's `openRepositoryFromUrl` → `openOrCloneRepository` flow reaches `clone()` in `app/src/lib/git/clone.ts`, which runs `git clone --recursive ... -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment.
4. Because Git's protected-clone check is disabled, the clone proceeds where an up-to-date, protection-enabled Git client would have aborted, allowing the crafted submodule content to land in/overwrite files such as the outer repository's `.git/hooks`, which then execute on the victim's machine on a subsequent Git operation (e.g., `git commit`, `git checkout`) performed by Desktop or the user.

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

**File:** app/src/lib/parse-app-url.ts (L10-24)
```typescript
export interface IOpenRepositoryFromURLAction {
  readonly name: 'open-repository-from-url'

  /** the remote repository location associated with the "Open in Desktop" action */
  readonly url: string

  /** the optional branch name which should be checked out. use the default branch otherwise. */
  readonly branch: string | null

  /** the pull request number, if pull request originates from a fork of the repository */
  readonly pr: string | null

  /** the file to open after cloning the repository */
  readonly filepath: string | null
}
```
