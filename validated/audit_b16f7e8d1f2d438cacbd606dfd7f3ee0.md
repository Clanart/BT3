## Title
Recursive `git clone` explicitly disables Git's embedded-repository clone protection, enabling attacker-controlled repositories to write files outside the intended working directory - (File: `app/src/lib/git/clone.ts`)

## Summary
GitHub Desktop's `clone()` function passes `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every recursive clone [1](#0-0) , while also always adding `--recursive` to the clone arguments [2](#0-1) . This is analogous to the reported bug class: a value that is supposed to guard an invariant (here, Git's own recent hardening against maliciously nested/symlinked repositories during recursive clone/submodule init) is explicitly forced to a "disabled" state at the moment of creation, just as the Solana program's `real_sol_reserves` invariant was silently defeated by a pre-existing attacker-controlled state.

## Finding Description
Modern Git (2.45.1+) introduced protections tracked via the `GIT_CLONE_PROTECTION_ACTIVE` mechanism to prevent a class of attacks where a cloned/fetched repository contains crafted submodules or embedded `.git` directories/symlinks that, when recursively initialized, cause Git to write files or execute hooks outside the top-level working directory that the user intended to clone into. GitHub Desktop's `clone()` helper unconditionally sets this variable to `'false'` for every clone operation [1](#0-0) , and always requests `--recursive` submodule initialization as part of the same command [2](#0-1) .

This mirrors the reported invariant bypass exactly: the code assumes a "clean slate" (an empty destination, well-formed nested repositories) but an attacker who controls the remote repository content (the GitHub-Desktop-analog of the attacker who pre-funds the escrow account) can craft `.gitmodules` entries or nested repository structures that Git's built-in protection would normally reject or safely handle - except that protection has been explicitly turned off by Desktop's own code before the clone begins. The application-level guards that exist elsewhere in this file - `isClonePathSensitive()` [3](#0-2)  and the empty-directory check in the UI (`validateEmptyFolder`) [4](#0-3)  - only validate the *destination* path chosen by the local user; they do nothing to constrain what the remote repository's content can do once recursive submodule cloning proceeds with Git's own embedded-repo protection deliberately disabled.

## Impact Explanation
If `GIT_CLONE_PROTECTION_ACTIVE=false` disables a real upstream Git safety check against malicious nested/embedded repositories during `--recursive` clone, an attacker who controls a remote repository (a GitHub repo, a spoofed/self-hosted remote, or a repo reached via `x-github-client://openRepo/...` deep link handled in `openRepositoryFromUrl`/`openOrCloneRepository` [5](#0-4) ) could cause Desktop to write or overwrite files outside the chosen clone directory during the automatic recursive submodule step, corrupting the user's filesystem state or planting files that get silently committed/pushed later. This satisfies the "file write outside the repo" / "silent corruption of what the user commits" impact categories.

## Likelihood Explanation
Likelihood is Medium: exploitation requires the victim to clone or "Open in Desktop" an attacker-controlled repository (a common, low-friction user action - cloning a public repo, following a "Clone in Desktop" link, or accepting a collaborator's fork), and requires that the specific Git protection gated by `GIT_CLONE_PROTECTION_ACTIVE` actually behaves as a meaningful guard against embedded/symlinked submodule content in the installed `dugite`/Git version. I could not verify from the indexed source alone what precise checks this flag disables in the vendored Git binary or `dugite`, since that logic lives inside the Git executable itself rather than in this repository.

## Recommendation
- Do not unconditionally disable `GIT_CLONE_PROTECTION_ACTIVE`. Only disable Git-side protections when there is a well-understood, narrow reason (e.g., a specific compatibility issue), and prefer scoping the exemption to that scenario only.
- If the flag was disabled to work around a benign warning/error for legitimate nested clones, replace it with a targeted fix (e.g., handling the specific error code) rather than a blanket disablement of the protection.
- Add automated tests that attempt to clone a fixture repository containing a submodule entry crafted to point outside the working directory (path traversal via `.gitmodules`, symlinked embedded `.git`) and assert that no files are written outside the clone destination.
- Audit all other call sites that set Git environment/config flags (e.g., `protocol.file.allow=always` in `submodule.ts` [6](#0-5) ) for the same "invariant defeated by our own initialization code" pattern.

## Proof of Concept
1. Set up a malicious remote repository whose `.gitmodules` file defines a submodule with a path/URL crafted to reference a nested/embedded repository structure that upstream Git's clone-protection mechanism (gated by `GIT_CLONE_PROTECTION_ACTIVE`) is designed to reject or neutralize.
2. Have the victim clone this repository through GitHub Desktop's `clone()` path (either via the Clone dialog, CLI `github clone`, or the `x-github-client://openRepo/...` deep link).
3. Observe that because `clone()` always sends `GIT_CLONE_PROTECTION_ACTIVE: 'false'` alongside `--recursive` [1](#0-0) , Git's protective check is bypassed during the automatic recursive submodule step, allowing the crafted repository content to affect files outside the destination directory chosen by `isClonePathSensitive`/`validateEmptyFolder`.

Note: I was unable to fully verify the exact runtime semantics of `GIT_CLONE_PROTECTION_ACTIVE` against the specific `dugite`/Git version vendored in this repo using the indexed source alone, since that behavior lives in the Git binary rather than this codebase. If precise confirmation of the disabled check's scope is needed, a Devin session with full repository and dependency access should inspect the vendored Git/`dugite` version's release notes and `setup.c` behavior for this variable.

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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L686-725)
```typescript
  private async validateEmptyFolder(
    path: string | null
  ): Promise<null | Error> {
    if (path === null) {
      return new Error(
        'Unable to read path on disk. Please check the path and try again.'
      )
    }

    try {
      const directoryFiles = await readdir(path)

      if (directoryFiles.length === 0) {
        return null
      } else {
        return new Error(
          'This folder contains files. Git can only clone to empty folders.'
        )
      }
    } catch (error) {
      if (error.code === 'ENOTDIR') {
        // path refers to a file or other file system entry
        return new Error(
          'There is already a file with this name. Git can only clone to a folder.'
        )
      }

      if (error.code === 'ENOENT') {
        // Folder does not exist
        return null
      }

      log.error(
        'CloneRepository: Path validation failed. Error: ' + error.message
      )
      return new Error(
        'Unable to read path on disk. Please check the path and try again.'
      )
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

**File:** app/src/lib/git/submodule.ts (L45-51)
```typescript
  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]
```
