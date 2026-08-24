[1](#0-0) 

### Title
Recursive clone explicitly disables Git's submodule protocol protection, allowing malicious repositories to exfiltrate local files via `file://`/`ext::` submodule URLs - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` unconditionally passes `--recursive` and sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every clone operation, regardless of who controls the remote content. This deliberately turns off the safety mechanism Git itself introduced (as a direct fix for CVE-2022-39253) to stop a cloned repository's own `.gitmodules` from silently fetching a submodule via `file://` (or other local/exotic transports), which can be used to read files from the user's machine into the resulting working tree.

### Finding Description
Only the clone *destination* path is validated: [2](#0-1) 
`isClonePathSensitive(path)` checks that the local folder being cloned *into* is not a sensitive OS location, but nothing validates the *content* of the untrusted remote being cloned. The same function then builds the clone command with: [3](#0-2) 
`GIT_CLONE_PROTECTION_ACTIVE: 'false'` combined with `'--recursive'`. This is the exact inverse of the blocklist bug pattern: the "source" (the untrusted repository content, i.e., its `.gitmodules` file) is never checked, while a downstream/local concept (destination path sensitivity) is. Git upstream added `GIT_CLONE_PROTECTION_ACTIVE`/`protocol.file.allow=user` specifically so that a repository cannot smuggle a submodule entry pointing at `file://` or other local-only URLs and have it silently checked out during a recursive clone — this was the fix for a real submodule file-disclosure CVE. Desktop explicitly re-enables the dangerous behavior for every clone by forcing this variable to `'false'`.

The same unrestricted pattern is repeated for post-checkout submodule updates via the `allowFileProtocol` flag, which — when `true` — passes `-c protocol.file.allow=always`: [4](#0-3) 

Nowhere in `clone()`, `checkoutBranch()`, or `checkoutCommit()` is the submodule URL scheme inspected or restricted before Git is invoked with these permissive settings: [5](#0-4) 

### Impact Explanation
An attacker who controls a public repository (e.g., links it for "Open in Desktop", or the user clones/adds it) can add a submodule entry in `.gitmodules` using `file://<path>` (or `ext::sh -c ...`-style transport helpers depending on the Git version installed) pointing to a path on the victim's disk. Because `GIT_CLONE_PROTECTION_ACTIVE` is forced off and `--recursive` is always used, Desktop's initial clone will happily initialize that submodule, effectively copying arbitrary local files into the cloned working tree. Once copied into the repo, that content can be viewed by the attacker if the victim ever commits/pushes, or read directly by the victim believing it's part of the project — a direct instance of "file read outside the repo" and potential subsequent "credential exfiltration" if the targeted files include tokens/config files.

### Likelihood Explanation
Any attacker-controlled repository can trigger this the moment a victim clones it or opens it via "Open in Desktop" (`x-github-client://` protocol) or the "Clone repository" dialog — no local access, malware, or social engineering beyond "clone this repo" is required, which is explicitly in-scope per the report's Valid Impact criteria ("attacker controls a cloned/fetched repository").

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE: 'false'` for the initial clone; let Git's default submodule-protocol protections apply (or explicitly set `protocol.file.allow=user`/`deny` and only relax it for `git@`/`https://` submodule URLs that are validated). Before enabling `allowFileProtocol` for submodule updates, validate that submodule URLs use expected remote protocols (https/ssh) and reject `file://`/`ext::`/other local-execution transports unless the repository was explicitly trusted by the user.

### Proof of Concept
1. Attacker creates a public repository containing a `.gitmodules` file with an entry such as:
   ```
   [submodule "leak"]
     path = leak
     url = file:///Users/victim/.ssh
   ```
2. Victim clones the repository through GitHub Desktop (drag-and-drop, Clone dialog, or "Open in Desktop" deep link).
3. `clone()` runs `git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false` set (`app/src/lib/git/clone.ts:81-93`), so Git does not block the `file://` submodule and copies the victim's local `~/.ssh` directory contents into the `leak/` folder of the freshly cloned repository.
4. The victim, unaware, may inspect, commit, or push the `leak/` directory, exposing local files that were never part of the intended repository content.

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

**File:** app/src/lib/git/submodule.ts (L29-51)
```typescript
export async function updateSubmodulesAfterOperation<T extends Progress>(
  repository: Repository,
  remote: IRemote | null,
  progressCallback: ((progress: T) => void) | undefined,
  progressKind: T['kind'],
  title: string,
  targetOrRemote: string,
  allowFileProtocol: boolean
): Promise<void> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, remote)
    ),
    expectedErrors: AuthenticationErrors,
  }

  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]
```

**File:** app/src/lib/git/checkout.ts (L102-146)
```typescript
export async function checkoutBranch(
  repository: Repository,
  branch: Branch,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
  const title = `Checking out branch ${branch.name}`
  const opts = await getCheckoutOpts(
    repository,
    title,
    branch.name,
    currentRemote,
    progressCallback
      ? clampProgress(0, CheckoutStepWeight, progressCallback)
      : undefined,
    `Switching to ${__DARWIN__ ? 'Branch' : 'branch'}`
  )

  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)

  // Update submodules after checkout
  await updateSubmodulesAfterOperation(
    repository,
    currentRemote,
    progressCallback
      ? clampProgress<ICheckoutProgress>(
          CheckoutStepWeight,
          1,
          progressCallback
        )
      : undefined,
    'checkout',
    title,
    branch.name,
    allowFileProtocol
  )

  // we return `true` here so `GitStore.performFailableGitOperation`
  // will return _something_ differentiable from `undefined` if this succeeds
  return true
}
```
