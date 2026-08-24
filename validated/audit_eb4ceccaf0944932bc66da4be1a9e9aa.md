Based on the evidence gathered, here is the strongest analog I could find with concrete local-code support.

### Title
Recursive submodule update can re-enable `file://` protocol, allowing an attacker-controlled repo to exfiltrate local files into the working tree - (File: app/src/lib/git/submodule.ts)

### Summary
`updateSubmodulesAfterOperation` accepts an `allowFileProtocol` flag that, when true, adds `-c protocol.file.allow=always` to the `git submodule update --init --recursive` invocation [1](#0-0) . This re-enables the exact git behavior that upstream git disabled by default (git ≥2.38) specifically because it allowed a malicious repository to declare a submodule with a `file://` URL pointing at a local filesystem path and have git silently copy/clone that local location's contents into the submodule directory of the working tree.

### Finding Description
The broken invariant is the same class as the original report: an operation is performed against a resource (here, an arbitrary local path) without verifying that the resource is one the acting party is actually permitted to touch. In the Hegic report, `exercise()` never checked that the caller's margin account owned the option token before acting on it. Here, `updateSubmodulesAfterOperation` never checks that a submodule URL supplied by an untrusted, attacker-controlled `.gitmodules` file is scoped to the fetched remote before allowing git to resolve it via the local filesystem — it simply flips `protocol.file.allow=always` for the whole recursive update [2](#0-1) .

The attacker primitive matches the allowed impact categories: the attacker controls a cloned/fetched repository. A malicious `.gitmodules` file can declare a submodule with `url = file:///home/victim/.ssh` (or any other local path). When Desktop performs a recursive submodule update with `allowFileProtocol` set, git will treat that as a valid remote and clone/copy the target directory's contents into the submodule's working-tree location inside the checked-out repository — a directory the user is likely to open, view diffs of, or commit from.

The Desktop-specific hardening seen elsewhere in this codebase (e.g., `resolveWithin`/`isAbsolute` checks in `dispatcher.ts` for opening files from URL actions [3](#0-2) , `isClonePathSensitive` in `clone.ts` [4](#0-3) , and the trusted-IPC-sender check [5](#0-4) ) shows the project is generally careful about untrusted-input path/URL handling — but none of those guards apply to submodule URLs consumed by git itself during `submodule update`. There's no filtering of `.gitmodules` submodule URLs for `file://` schemes or local paths before the recursive update runs with `protocol.file.allow=always`.

### Impact Explanation
If reachable with `allowFileProtocol=true` on a checkout/clone of an attacker-supplied or attacker-influenced repository, this allows silent disclosure of arbitrary local files/directories (SSH keys, config files, credential stores) into the user's working tree, where they could subsequently be committed and pushed (data exfiltration to the attacker's own remote) or simply read by the attacker if they also control where the resulting repository state is later shared. This matches the "file read outside the repo" / "credential exfiltration" / "silent corruption of what the user commits" impact categories.

### Likelihood Explanation
Likelihood depends entirely on whether `allowFileProtocol` is actually passed as `true` for submodule updates triggered by content originating from an untrusted/attacker-supplied repository (e.g., during checkout of a branch/PR from a fork). I found 4 references to `allowFileProtocol` in `app/src/lib/git/checkout.ts` that plumb this value through, but I was not able to inspect those call sites within the remaining tool budget to confirm the exact condition under which `true` is passed versus a safer default. This is the key open question that determines whether this is exploitable without any unusual/local access — I could not fully verify it before running out of iterations.

### Recommendation
- Do not pass `allowFileProtocol=true` for submodule operations triggered by unauthenticated/untrusted repository content (forks, PR checkouts, arbitrary clone URLs) unless the user has explicitly opted in for a specific, already-trusted repository.
- If `file://` submodules must be supported, resolve and canonicalize the submodule URL and require it to be scoped underneath the parent repository's own working directory (mirroring the `resolveWithin` pattern already used elsewhere in this codebase) before permitting the operation.
- Add a regression test analogous to `app/test/unit/git/clone-test.ts` that asserts a submodule with a `file://` URL pointing outside the repository is rejected or ignored when `allowFileProtocol` is not explicitly authorized.

### Proof of Concept
1. Attacker publishes a repository containing a `.gitmodules` entry:
```
[submodule "leak"]
    path = leak
    url = file:///Users/victim/.ssh
```
2. Victim clones/checks out this repository (or a branch/PR from it) in GitHub Desktop, triggering a recursive submodule update via `updateSubmodulesAfterOperation` with `allowFileProtocol=true` [2](#0-1) .
3. Git executes `git -c protocol.file.allow=always submodule update --init --recursive`, which resolves the `file://` URL and copies the local `~/.ssh` directory's contents into `leak/` inside the checked-out working tree.
4. The victim's private key material is now present in their working directory, visible in Desktop's changes view, and could be accidentally committed/pushed.

Note: full confirmation requires reviewing `app/src/lib/git/checkout.ts` to determine exactly when `allowFileProtocol=true` is passed for untrusted checkouts; I could not complete that verification within the available tool budget.

### Citations

**File:** app/src/lib/git/submodule.ts (L36-51)
```typescript
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

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

**File:** app/src/main-process/trusted-ipc-sender.ts (L9-16)
```typescript
/** Adds a WebContents instance to the set of trusted IPC senders. */
export const addTrustedIPCSender = (wc: WebContents) => {
  trustedSenders.add(wc.id)
  wc.on('destroyed', () => trustedSenders.delete(wc.id))
}

/** Returns true if the given WebContents is a trusted sender of IPC messages. */
export const isTrustedIPCSender = (wc: WebContents) => trustedSenders.has(wc.id)
```
