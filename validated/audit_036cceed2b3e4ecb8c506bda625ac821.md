### Title
Clone operation explicitly disables Git's `GIT_CLONE_PROTECTION_ACTIVE` safety check while running `--recursive` clone of an attacker-controlled repository - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` sets the environment variable `GIT_CLONE_PROTECTION_ACTIVE: 'false'` unconditionally on every clone, and simultaneously always passes `--recursive` to `git clone`. `GIT_CLONE_PROTECTION_ACTIVE` is the escape hatch Git itself ships specifically to let embedders explicitly opt out of the clone-time safety checks that were added to Git core to prevent maliciously crafted repositories (case-confusable/`..`-containing paths, `.git`-like submodule directories, symlinked submodule working trees etc.) from writing files or hooks outside the intended clone destination during clone/checkout of the top-level repo and its submodules. Desktop turns this protection off for every single clone from an untrusted, attacker-controlled URL.

### Finding Description
`clone()` builds the execution environment as:
```
app/src/lib/git/clone.ts:81-84
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
```
and then always adds `--recursive` to the argument list:
```
app/src/lib/git/clone.ts:88-93
const args = [
  '-c',
  `init.defaultBranch=${defaultBranch}`,
  'clone',
  '--recursive',
]
```
`url` is fully attacker/user controlled (it is the clone URL entered by the user or received via a deep link / "Open in Desktop" action, per `openOrCloneRepository` in `app/src/ui/dispatcher/dispatcher.ts`), so the content, submodule list, and submodule paths of the resulting repository are entirely controlled by whoever hosts that remote.

Git's `GIT_CLONE_PROTECTION_ACTIVE` variable is the internal switch Git added to guard clone (and, transitively, the recursive submodule clone/checkout that follows it) against maliciously structured repositories — e.g. submodule paths that collide with `.git`, use path traversal, or resolve through symlinks to write files outside the intended working tree, which historically enabled hook/file planting outside the repo. Setting it to `'false'` deliberately disables that guard for every clone Desktop performs, including the recursive submodule step (`--recursive`), which is exactly the code path the guard is designed to protect.

The repository does have other hardening in this same file (`isClonePathSensitive`, blocking `~/.ssh`, `~/.gnupg`, `~/.config`, `~/.gitconfig`, home root, etc.), but these only validate the *top-level destination directory* chosen by the user. They do nothing to constrain where the submodules embedded inside an attacker's repository (fetched via `--recursive`) are written relative to the repository once the clone target itself is not "sensitive" — that is precisely the class of write-outside-tree issue `GIT_CLONE_PROTECTION_ACTIVE` exists to stop, and it has been explicitly turned off.

### Impact Explanation
If the embedded Git version's protection would have rejected or sanitized a maliciously crafted submodule layout (colliding/symlinked/`..`-containing submodule paths) during the `--recursive` clone, disabling `GIT_CLONE_PROTECTION_ACTIVE` removes that backstop entirely. A malicious repository author can craft a repo (with submodules) that, once cloned by Desktop with this protection off, writes files or a `.git` directory structure outside the intended cloned working tree — potentially onto arbitrary filesystem locations reachable by symlink/path tricks, which can lead to file write outside the repo and, depending on what gets written (e.g. a hooks directory or executable), subsequent code execution. This satisfies the report's valid-impact criteria: attacker controls a cloned repository URL, no local/physical access or prior compromise is required, and the outcome is file write outside the repo / potential code execution.

### Likelihood Explanation
Any user can trigger this by cloning a URL supplied by an attacker (including via "Open in Desktop" deep links handled by `openOrCloneRepository`/`dispatchURLAction` in `app/src/ui/dispatcher/dispatcher.ts`), which is a completely normal, expected user action requiring no unusual steps. Since `GIT_CLONE_PROTECTION_ACTIVE: 'false'` is hard-coded and applied unconditionally to every clone (not just a legacy compatibility fallback), the disabled protection is always in effect, maximizing the exploitation window whenever the app's bundled Git version implements checks gated by that variable.

### Recommendation
Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override from `clone()` so Git's built-in clone-time protections remain active for both the top-level clone and the `--recursive` submodule clone. If protection has to be disabled for a specific compatibility reason, that reason should be documented, scoped as narrowly as possible (e.g., only for trusted/local clones), and paired with equivalent explicit validation of every submodule path against path traversal/symlink escape (e.g., via the existing `resolveWithin`/`sanitizeCloneName` helpers already used elsewhere in this codebase) before it is safe to disable the native Git guard.

### Proof of Concept
1. Attacker publishes a Git repository containing a `.gitmodules` file with a submodule whose path is crafted to alias `.git`, contain traversal segments, or resolve through a symlinked working tree — the specific payload shape needed depends on the exact Git version bundled with Desktop and the specific check(s) gated behind `GIT_CLONE_PROTECTION_ACTIVE`.
2. Attacker sends the victim a link to clone the repository via "Open in Desktop" (`x-github-client://openRepo/...`) or simply shares the clone URL.
3. Victim clicks "Clone" in Desktop's Clone dialog; Desktop calls `clone(url, path, options, ...)` in `app/src/lib/git/clone.ts`, which executes `git … clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set, bypassing Git's built-in protection during the recursive submodule fetch/checkout.
4. If the bundled Git's protection (which the app has disabled) would otherwise have rejected/sanitized the malicious submodule layout, the crafted submodule paths are written unchecked, potentially escaping the intended clone directory.

Note: I was not able to determine from the indexed code alone the exact `dugite`/embedded Git version shipped with this build (no `package.json` entry was found by search), so I cannot confirm which specific CVE-gated check(s) `GIT_CLONE_PROTECTION_ACTIVE` disables in that exact version. This is a real, unconditional disabling of a Git safety mechanism during recursive clones of attacker-controlled URLs — I recommend a Devin session with full filesystem/terminal access to pin down the bundled Git version and construct a concrete working payload/PoC for confirmation. [1](#0-0) [2](#0-1)

### Citations

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
