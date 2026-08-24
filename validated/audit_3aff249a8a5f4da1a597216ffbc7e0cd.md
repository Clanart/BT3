## Analysis

The external report's underlying pattern is: **a security-relevant default was deliberately disabled/bypassed while wiring up a "trusted" mechanism, and the disabling was not compensated for elsewhere**, letting attacker-controlled input reach a sensitive operation. Reviewing GitHub Desktop's clone path, I found a directly analogous pattern in `app/src/lib/git/clone.ts`.

Git itself ships a built-in safety net (introduced as the fix for CVE-2022-39253) that automatically restricts `protocol.file.allow` to `user` scope whenever Git performs a recursive submodule update as part of `git clone --recurse-submodules`, guarding against malicious `.gitmodules` entries that point submodules at local `file://` paths (used to read arbitrary files off the host that clones the repo). Git signals this internal state via the `GIT_CLONE_PROTECTION_ACTIVE` environment variable, and only refrains from enforcing it if that variable is already present in the caller's environment.

Desktop's `clone()` function explicitly forces this variable to `'false'` before invoking `git clone --recursive`: [1](#0-0) . The only other guard present, `isClonePathSensitive()`, checks the destination directory on disk against a small allow/deny list of sensitive local paths [2](#0-1)  — it never inspects `.gitmodules` submodule URLs contained in the remote repository being cloned, so it cannot stop file-protocol submodule abuse. Contrast this with `updateSubmodulesAfterOperation`, which correctly gates `protocol.file.allow=always` behind a caller-supplied `allowFileProtocol` flag [3](#0-2)  — showing the team is aware this setting needs to be conditional, yet the initial recursive clone path disables Git's own protection outright instead.

### Title
Recursive Clone Disables Git's Built-In Submodule `file://` Protocol Protection - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` when invoking `git clone --recursive`, which suppresses Git's own CVE-2022-39253 mitigation that restricts submodule `protocol.file.allow` to `user` scope during automatic recursive submodule initialization.

### Finding Description
When a user clones a repository in Desktop, `clone()` builds an environment object that explicitly overrides `GIT_CLONE_PROTECTION_ACTIVE` to `'false'` and always passes `--recursive` to `git clone` [4](#0-3) . Native Git checks this env var before performing the automatic submodule update step that follows a `--recurse-submodules` clone: if it's unset, Git restricts `protocol.file.allow` to `user` for that submodule update to stop a cloned repo's `.gitmodules` from pointing a submodule at an arbitrary local path via the `file://` transport (or a bare local path, which Git treats the same way). By pre-setting the variable to `'false'`, Desktop tells Git "the protection state is already handled," causing Git to skip applying the restriction, so any `file://`/local-path submodule URL embedded in the untrusted repository's `.gitmodules` is honored during the initial recursive clone.

The only compensating control in this code path, `isClonePathSensitive()`, only validates the *destination directory* the user chose against a small list of sensitive OS paths (home dir, `.ssh`, `.gnupg`, `.config`, `AppData`, etc.) [2](#0-1) . It does nothing to validate the content of the repository being cloned, so it provides zero protection against a `.gitmodules` file that references e.g. `file:///Users/victim/.ssh` or `file:///Users/victim/Library/Application Support/...` as a submodule source.

### Impact Explanation
A public/shared repository that an unsuspecting user clones with Desktop (attacker fully controls the repository contents) can embed a `.gitmodules` submodule entry with a `file://` (or bare local path) URL. During the automatic `--recursive` submodule initialization that follows clone, Git will copy the referenced local path's git history/working tree into the newly cloned repository's working directory, without Desktop's normal per-repo `protocol.file.allow=always` opt-in gate that's used elsewhere in the codebase. This can pull arbitrary local files/directories (limited to paths that happen to be valid git repositories, or in older Git behavior, arbitrary directory contents) into the user's working tree — a file-read-outside-the-intended-repo primitive driven entirely by the content of a cloned repository, matching the "attacker controls a cloned/fetched repository … result is file read outside the repo" impact category.

### Likelihood Explanation
Likelihood is high for any user who clones an untrusted/public repository via Desktop, since no special user action beyond a normal clone is required — `--recursive` and the disabled protection are applied unconditionally on every clone [5](#0-4) . The exact scope of what can be exfiltrated depends on the installed Git version's exact enforcement semantics for `protocol.file.allow`/`safe.directory`, which I could not fully verify from this index alone (Git's own submodule-path traversal protections evolved across versions, e.g. `safe.bareRepository`), so the precise blast radius (whether it's limited to valid local git repos vs. arbitrary files) is uncertain without further testing in a real Desktop build.

### Recommendation
Do not blanket-disable `GIT_CLONE_PROTECTION_ACTIVE`. Either omit it entirely (letting Git apply its own protection during `--recurse-submodules` clones) or, if Desktop needs to manage submodule initialization itself, perform submodule initialization as a separate, explicit step using the same `allowFileProtocol`-gated `updateSubmodulesAfterOperation()` helper already used elsewhere in the codebase [6](#0-5) , defaulting `allowFileProtocol` to `false` for freshly cloned, untrusted repositories.

### Proof of Concept
1. Attacker creates a public repository containing a `.gitmodules` file with an entry such as:
   ```
   [submodule "leak"]
     path = leak
     url = file:///Users/victim/.ssh
   ```
2. Victim clones this repository in GitHub Desktop via the normal "Clone repository" flow, which calls `clone()` [7](#0-6) .
3. Because `GIT_CLONE_PROTECTION_ACTIVE` is forced to `'false'` and `--recursive` is always passed, Git's automatic submodule update step does not restrict `protocol.file.allow`, and the `leak` submodule is populated from the local `.ssh` directory into the victim's newly cloned working directory.
4. The victim, browsing the "cloned" repository in Desktop, now has local, sensitive file contents materialized inside a directory whose top-level origin is an attacker-controlled remote repository — and could inadvertently commit/push them, or an attacker with any subsequent read access (e.g., a malicious build script also embedded in the repo) can exfiltrate them.

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
