### Title
`safe.directory` trust exception is a permanent, unrevoked global grant that lets an attacker who later controls a previously-trusted path hijack Git operations - ([File: app/src/lib/git/config.ts])

### Summary
The Hats Protocol bug is a stale-authorization pattern: a "grant" (`linkedTreeRequests`) created while a trust relationship exists is never cleared when that relationship is torn down (`unlinkTopHatFromTree`), so it can be redeemed later by someone who no longer should have any claim on the asset. GitHub Desktop has the same structural flaw around its "Trust Repository" feature for directories Git considers to have "dubious ownership". `addSafeDirectory()` in [1](#0-0)  permanently appends the filesystem *path* to the user's **global** `safe.directory` git config. There is no corresponding `removeSafeDirectory`/revoke function anywhere in the codebase, and no code path removes the entry when the repository is removed from Desktop, relocated, or the directory is later deleted and recreated.

### Finding Description
When Desktop encounters a directory that Git flags as owned by a different user (`getRepositoryType` returning `{ kind: 'unsafe', path }` from the `fatal: detected dubious ownership` message) in [2](#0-1) , the UI offers a one-time "Trust Repository" action in both the add-existing-repository flow and the missing-repository flow: [3](#0-2) [4](#0-3) 

Both call `addSafeDirectory(path)`, which unconditionally and permanently records the *path string* (not a repository identity, not a commit hash, not any binding to the content that was inspected) into the global `~/.gitconfig`: [5](#0-4) 

This is functionally identical to `requestLinkTopHatToTree` — a trust grant is written to persistent state at a moment when the relationship (the specific repo occupying that path) seems legitimate. But:
- Desktop's repository removal flow (`_removeRepository` / relocate) never calls any function to `--unset` or scope-limit the `safe.directory` entry.
- A repository search across the entire codebase confirms `addSafeDirectory` has no counterpart `removeSafeDirectory`/`deleteSafeDirectory` — the "unlink" step from the Hats analogy (revoking a stale grant) simply does not exist.
- The `_relocateRepository` code explicitly acknowledges that a repository at a path can change identity/ownership over time (it re-derives `topLevelWorkingDirectory` and drops stale info for `unsafe` repos), yet it still does not revoke the global trust for the *old* path: [6](#0-5) 

Because `safe.directory` is a path-based (not content- or ownership-hash-based) exception in Git, once a path is trusted, **any** future Git repository that later occupies that same path — regardless of who created or owns it — bypasses Git's dubious-ownership protection entirely. That protection exists specifically to stop an attacker-controlled repository from having its local (per-repo) `.git/config` respected, which can set attacker-controlled `core.hooksPath` and other settings that execute code on ordinary Git operations Desktop performs constantly (status, fetch, commit, checkout) via `withHooksEnv`/hook execution: [7](#0-6) 

### Impact Explanation
A directory path that a user legitimately trusted once (e.g., a shared network drive, a removable volume, a multi-user machine home directory, a container bind-mount, or a path later reused after the original repository was removed from Desktop and the folder deleted) remains permanently exempt from Git's ownership safety check. If that same path is later repopulated with an attacker-controlled Git repository (different OS owner, different content, potentially with `core.hooksPath` pointing at a malicious script, or dubious `include.path` directives in the repo's local config), Desktop will silently perform Git operations against it — including running hooks — without ever re-prompting the user, because the app has no mechanism to invalidate the earlier "Trust Repository" decision. This can lead to arbitrary code execution outside of any sandbox and undermines the very "dubious ownership" mitigation Git added for CVE-class attacks that this feature exists to gate.

### Likelihood Explanation
This requires a realistic but non-trivial precondition: the same filesystem path must later come under a different/attacker owner (shared machines, network shares, containers/dev environments, USB drives, CI runners with reused workspace paths, or WSL/Windows path collisions) — scenarios already explicitly supported and called out by Desktop's own changelog entries for network-share and UNC-path safe-directory support (`changelog.json`: "Support trusting repositories on network shares (Windows)"). No local/physical/admin access to the *victim's* Desktop install is required beyond what the "safe.directory" feature is already designed to be used for; the attacker only needs to control content that later occupies the previously-trusted path.

### Recommendation
- Do not persist `safe.directory` trust indefinitely and un-scoped to a specific repository identity. Consider recording the trust decision keyed by repository path together with something stable to the *content* (e.g. the initial commit or root tree hash) or, at minimum, tie it to the Desktop repository record so that when the repository entry is removed (`removeRepository`) or relocated away from that path, the app calls a symmetrical `removeSafeDirectory(path)` (`git config --global --unset-all safe.directory <path>`).
- Re-validate trust (re-prompt) if the underlying repository's origin/remote or first-commit identity changes at a previously-trusted path.
- Add the missing `removeSafeDirectory` function to `app/src/lib/git/config.ts` and invoke it from repository removal/relocation flows, mirroring the Hats fix of deleting `linkedTreeRequests` in `unlinkTopHatFromTree`.

### Proof of Concept
1. On a shared or removable-storage path `P`, user adds an existing legitimate repository; Git reports dubious ownership; user clicks "Trust Repository" → `addSafeDirectory(P)` permanently adds `P` to global `safe.directory`. [3](#0-2) 
2. User later removes the repository from Desktop (or the storage device path `P` is reassigned/reused, e.g., a different USB drive mounted at the same drive letter, or a different user's home directory synced to the same UNC share path).
3. Attacker places a malicious Git repository at path `P` containing a `.git/config` with `core.hooksPath` (or similar) pointing to an attacker script.
4. Victim reopens/re-adds a repository at path `P` in Desktop (or Desktop still has a stale reference and refreshes it). Because `P` is already listed in global `safe.directory`, `getRepositoryType` in [8](#0-7)  no longer reports `unsafe` — Desktop proceeds straight to normal Git operations (status/fetch/commit), triggering the attacker's hook without any additional user confirmation.

### Citations

**File:** app/src/lib/git/config.ts (L176-189)
```typescript
/**
 * Adds a path to the `safe.directories` configuration variable if it's not
 * already present. Adding a path to `safe.directory` will cause Git to ignore
 * if the path is owner by a different user than the current.
 */
export async function addSafeDirectory(path: string) {
  // UNC-paths on Windows need to be prefixed with `%(prefix)/`, see
  // https://github.com/git-for-windows/git/commit/e394a16023cbb62784e380f70ad8a833fb960d68
  if (__WIN32__ && path[0] === '/') {
    path = `%(prefix)/${path}`
  }

  await addGlobalConfigValueIfMissing('safe.directory', path)
}
```

**File:** app/src/lib/git/rev-parse.ts (L18-65)
```typescript
export async function getRepositoryType(path: string): Promise<RepositoryType> {
  if (!(await directoryExists(path))) {
    return { kind: 'missing' }
  }

  try {
    const result = await git(
      ['rev-parse', '--is-bare-repository', '--show-cdup', '--git-dir'],
      path,
      'getRepositoryType',
      { successExitCodes: new Set([0, 128]) }
    )

    if (result.exitCode === 0) {
      // Bare repositories will not include gitdir so we handle that separately
      if (result.stdout.startsWith('true\n')) {
        return { kind: 'bare' }
      }

      // --is-bare-repository and --show-cdup each produce a single line but
      // --git-dir could theoretically contain newlines so we parse the known
      // fields first and treat the remainder as the git dir. We use [\s\S]*
      // instead of .* for the git dir capture group because .* doesn't match
      // newlines whereas [\s\S]* matches any character including newlines.
      const match = result.stdout.match(/^(true|false)\n(.*)\n([\s\S]*)\n$/)

      if (match) {
        const [, isBare, cdup, gitDir] = match

        return isBare === 'true'
          ? { kind: 'bare' }
          : {
              kind: 'regular',
              topLevelWorkingDirectory: resolve(path, cdup),
              gitDir: resolve(path, gitDir),
            }
      }
    }

    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }

    return { kind: 'missing' }
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L69-77)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingRepository: true })
    const { repositoryUnsafePath, path } = this.state
    if (repositoryUnsafePath) {
      await addSafeDirectory(repositoryUnsafePath)
    }
    await this.validatePath(path)
    this.setState({ isTrustingRepository: false })
  }
```

**File:** app/src/ui/missing-repository.tsx (L35-50)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingPath: true })
    const { unsafePath } = this.state
    const { repository } = this.props

    if (unsafePath) {
      await addSafeDirectory(unsafePath)
      const type = await getRepositoryType(repository.path)

      this.setState({ isTrustingPath: false })

      if (type.kind !== 'unsafe') {
        this.checkAgain()
      }
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L8176-8208)
```typescript
  public async _relocateRepository(repository: Repository): Promise<void> {
    const path = await showOpenDialog({ properties: ['openDirectory'] })

    if (path === null) {
      return
    }

    const rt = await getRepositoryType(path)

    if (rt.kind === 'regular') {
      // The repository has moved, so any main worktree we recorded before now
      // points at where it used to be. Resolve it again from the new location.
      await this.repositoriesStore.updateRepositoryPath(
        repository,
        rt.topLevelWorkingDirectory,
        rt.gitDir,
        await this.findMainWorktreePath(rt.topLevelWorkingDirectory)
      )
    } else if (rt.kind === 'unsafe') {
      // Git refuses to run in a repository it considers unsafe, so there's no
      // resolving the main worktree here. Drop the recorded path rather than
      // keep one we know is stale.
      await this.repositoriesStore.updateRepositoryPath(
        repository,
        path,
        undefined,
        undefined,
        true
      )
    } else {
      this.emitError(new Error(this.getInvalidRepoPathsMessage([path])))
    }
  }
```

**File:** app/src/lib/git/core.ts (L11-19)
```typescript
import { assertNever } from '../fatal-error'
import * as GitPerf from '../../ui/lib/git-perf'
import * as Path from 'path'
import { isErrnoException } from '../errno-exception'
import { withTrampolineEnv } from '../trampoline/trampoline-environment'
import { kStringMaxLength } from 'buffer'
import { withHooksEnv } from '../hooks/with-hooks-env'
import { coerceToString } from './coerce-to-string'
import { pushTerminalChunk } from './push-terminal-chunk'
```
