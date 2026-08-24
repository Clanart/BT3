### Title
Clone operations explicitly disable Git's built-in clone-time protections (`GIT_CLONE_PROTECTION_ACTIVE=false`) against malicious repository content - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` in `app/src/lib/git/clone.ts` sets the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to `'false'` for every clone operation, unconditionally. [1](#0-0) 
This variable is the runtime kill-switch for Git's clone-time protections (introduced to guard against attacker-controlled repository content that abuses symlinked/aliased `.git` directories, case-insensitive filesystem collisions, and other clone-time filesystem tricks). By forcing this protection off on every clone, GitHub Desktop removes an upstream Git defense specifically designed to guard against hostile repositories being cloned by an unsuspecting user — which is exactly the attacker model this task requires (an attacker-controlled remote/repository that the victim clones).

### Finding Description
Desktop's `clone()` function builds the environment for the `git clone` subprocess via `envForRemoteOperation(url)`, then unconditionally overrides it with `GIT_CLONE_PROTECTION_ACTIVE: 'false'`: [1](#0-0) 

This env var is passed straight through to the `git` CLI process for the `clone` command with the `--recursive` flag: [2](#0-1) 

Notably, the same file already contains a defense-in-depth patch (`isClonePathSensitive`) aimed at preventing crafted URLs from directing a clone into sensitive locations such as `~/.ssh` or `~/.gnupg`: [3](#0-2) 

This shows the maintainers of this fork are actively hardening `clone()` against attacker-controlled repository/URL content — yet the same function simultaneously disables Git's own clone-time protection mechanism for the entire operation, including the `--recursive` submodule clone. Combined with `updateSubmodulesAfterOperation` / `listSubmodules` in `app/src/lib/git/submodule.ts`, which trust `.gitmodules` and submodule paths coming straight from a cloned/attacker-controlled repository, this widens the surface an attacker-controlled repository has during the initial clone. [4](#0-3) 

There is no local flag, feature check, or configuration path that re-enables the protection for untrusted or first-time clones (e.g., cloning a URL a user pasted from a link, deep link, or unfamiliar remote) — the override is applied identically for every clone regardless of trust level.

### Impact Explanation
Git's clone-time protection (gated by `GIT_CLONE_PROTECTION_ACTIVE`) exists specifically to stop crafted repository content from tricking the clone process on disk (e.g. via symlink or case-folding collisions inside `.git`/`.git/modules`), which upstream Git added as a defense against repository-borne file write/execution primitives during checkout of untrusted content. Disabling it unconditionally reintroduces that class of risk for every user who clones or re-clones an attacker-supplied URL (pasted link, "Open in Desktop" deep link via `parseAppURL`/`openrepo` action, or a malicious/compromised remote). A successful exploit in this class can result in file writes outside the intended repository directory or execution of attacker-controlled content during/after clone — matching the "file write or read outside the repo" / "code execution" impact bar for this scan.

### Likelihood Explanation
The override is unconditional and applies to every clone path in the app, including the deep-link/CLI clone flow (`open-repository-from-url`, `cli-clone`) where the URL is fully attacker-controlled and the user's only action is clicking a link or running a provided clone command. [5](#0-4) [6](#0-5) 
No additional local access, admin rights, or unnatural user steps are required beyond the normal "clone this repository" action that Desktop is designed for.

### Recommendation
Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `clone()`, or scope it strictly to internal, fully-trusted operations if it exists to work around a specific compatibility issue; document the exact reason it was added and gate it behind an explicit, narrowly-scoped condition rather than applying it to all clone traffic, especially clones originating from URLs/deep links that the user did not type/verify themselves.

### Proof of Concept
1. Register/attacker-host a malicious repository whose tree, when cloned, would normally be caught by Git's clone-time protection (e.g., a crafted `.git`/`.gitmodules`/submodule layout intended to trigger a filesystem collision or symlink trick during `--recursive` clone).
2. Have the victim click an `x-github-client://openRepo/<attacker-url>` deep link (handled by `parseAppURL`/`dispatchURLAction`) or provide the URL to Desktop's clone dialog. [7](#0-6) 
3. Desktop calls `clone(url, path, options)`, which spawns `git clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` set in the environment. [8](#0-7) 
4. Because the protection this variable controls is disabled, Git performs the clone without its dedicated guard against the malicious repository layout, unlike a default Git client/other Git front-ends that leave this protection enabled — reproducing, in Desktop, exactly the class of clone-time hazard the upstream Git protection was introduced to close.

Note: I was unable to find any explicit code comment, changelog entry, or test in this indexed snapshot explaining why `GIT_CLONE_PROTECTION_ACTIVE` is disabled, so it's possible this was intentionally added to work around a benign compatibility issue (e.g., with LFS or a CI environment) rather than a pure oversight — a Devin session with full repo/history access would be needed to confirm the original intent and whether narrowing its scope is safe.

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

**File:** app/src/lib/git/submodule.ts (L1-29)
```typescript
import { git, IGitStringExecutionOptions } from './core'
import { Repository } from '../../models/repository'
import { SubmoduleEntry } from '../../models/submodule'
import { pathExists } from '../path-exists'
import { executionOptionsWithProgress, IGitOutput } from '../progress'
import {
  envForRemoteOperation,
  getFallbackUrlForProxyResolve,
} from './environment'
import { AuthenticationErrors } from './authentication'
import { IRemote } from '../../models/remote'
import { Progress } from '../../models/progress'
import { join, resolve } from 'path'
import { readFile } from 'fs/promises'

/**
 * Update submodules after a git operation.
 *
 * @param repository - The repository in which to update submodules
 * @param remote - The remote for environment setup (can be null)
 * @param progressCallback - An optional function which will be invoked
 *                           with information about the current progress
 *                           of the submodule update operation.
 * @param progressKind - The kind of progress event ('checkout', 'pull', etc.)
 * @param title - The title to use for progress reporting
 * @param targetOrRemote - The target (for checkout) or remote name (for pull)
 * @param allowFileProtocol - Whether to allow file:// protocol for submodules
 */
export async function updateSubmodulesAfterOperation<T extends Progress>(
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```
