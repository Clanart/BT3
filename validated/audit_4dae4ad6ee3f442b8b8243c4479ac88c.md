## Analysis Result

The bug-class in the report is: **an attacker-controlled write to a value that a downstream safety check relies on, performed inside a flow the app treats as routine/trusted, silently defeating the guard.** In the Solidity report, `_sendYieldToVault()`/`depositProfitTokenForUsers()` let an untrusted, permissionless caller overwrite `lastProfitTime`, neutering the check meant to gate `withdrawProfit()`.

Searching the Desktop codebase for a comparable pattern — an attacker-controlled input flowing into a call that silently disables an existing security guard — surfaces a real analog in the clone path.

### Title
GIT_CLONE_PROTECTION_ACTIVE is explicitly disabled for every recursive clone, re-enabling the CVE‑2024‑32002 submodule RCE class - ([File: app/src/lib/git/clone.ts])

### Summary
`clone()` unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every `git clone --recursive` invocation Desktop performs, regardless of whether the URL/remote is attacker-controlled. [1](#0-0) 

### Finding Description
`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git itself introduced as part of the fix for the recursive-submodule clone RCE (the class of vulnerability fixed upstream around Git 2.45.1/CVE-2024-32002), which allowed a malicious repository with crafted nested/symlinked submodule paths on case-insensitive or symlink-following filesystems to write files (including hooks) outside the intended checkout directory during a recursive clone, leading to code execution. Git's own protection refuses such clones unless this variable is explicitly set to disable the guard.

Desktop's `clone()` function always passes `'--recursive'` and always sets this variable to `'false'` for every clone, including clones the user initiates from an arbitrary/attacker-supplied URL (e.g., via `openRepositoryFromUrl`, `dispatchCLIAction`, or the "Clone repository" dialog): [2](#0-1) [3](#0-2) 

This mirrors the report's broken invariant exactly: a safety check (`lastProfitTime` gate / Git's clone-time submodule-path protection) is turned off from a code path that an attacker-controlled object (a malicious repository being cloned) can reach, and the disabling happens silently as part of routine, permissionless usage (a normal "clone" click) rather than an explicit, reviewed opt-out.

Existing Desktop guards do not stop this path:
- `isClonePathSensitive()` only validates the **top-level destination directory** the user chose (e.g., not `~/.ssh`); it does nothing to validate the paths Git will materialize for nested submodules during the recursive clone, which is exactly what Git's now-disabled protection was designed to catch. [4](#0-3) 
- `resolveWithin()` / path-traversal guards are applied elsewhere in the app (e.g., `openRepositoryFromUrl`'s filepath handling, Copilot conflict resolution) but are never consulted during the native `git clone --recursive` process itself, which is spawned as a single opaque `git` invocation. [5](#0-4) 
- The "unsafe repository" ownership check (`getRepositoryType` → `kind: 'unsafe'`) only fires for **existing local repositories being added**, not for the destination of a fresh clone, so it provides no protection here. [6](#0-5) 

### Impact Explanation
An attacker who controls the repository being cloned (a normal, unprivileged attack surface — anyone can publish a public Git repo or send a clone URL/deep link) can craft `.gitmodules`/nested submodule structures designed to exploit the exact clone-time file-placement bug that `GIT_CLONE_PROTECTION_ACTIVE` was created to prevent. With the guard force-disabled, Desktop's `git clone --recursive` proceeds where stock Git would refuse, potentially resulting in file writes outside the intended repository directory and, depending on filesystem case-sensitivity/symlink behavior, execution of attacker-supplied hook scripts — i.e., code execution or file write outside the repo, both explicitly in-scope impacts.

### Likelihood Explanation
The vulnerable code path is unconditional and always reached whenever a user clones any repository through Desktop (including via the "Open in Desktop" deep link / `openrepo` protocol handler and CLI `--cli-clone`), so no unusual user behavior is required beyond the normal, expected action of cloning a link/repo the attacker provided. The only variable is whether the target OS/filesystem combination is susceptible to the underlying Git-level bug the flag was meant to guard against (case-insensitive or symlink-permissive filesystems), which is a platform condition, not a user behavior condition.

### Recommendation
Do not set `GIT_CLONE_PROTECTION_ACTIVE: 'false'`; let Git's clone-time protection remain active for all Desktop-initiated clones. If recursive submodule cloning must proceed despite the protection triggering, surface the failure to the user (similar to how `isClonePathSensitive` failures are surfaced) instead of silently suppressing the underlying Git safety mechanism.

### Proof of Concept
1. Attacker publishes a public repository with a `.gitmodules` file and nested submodule/`.git` structure crafted to trigger the recursive-clone submodule path-confusion bug that `GIT_CLONE_PROTECTION_ACTIVE` guards against (case-insensitive path collision with `.git`/hooks directories or symlinked submodule paths).
2. Attacker sends the victim a normal clone URL or an `x-github-client://openRepo/<attacker-url>` deep link.
3. Victim clones the repository through Desktop's normal "Clone repository" flow or by clicking the link; `clone()` is invoked with `--recursive` and `GIT_CLONE_PROTECTION_ACTIVE=false`. [1](#0-0) 
4. Because Desktop has disabled Git's own protection, the recursive submodule clone proceeds past the check Git would otherwise perform, allowing the crafted repository to place/overwrite files outside the intended repository directory (e.g., into `.git/hooks`), which subsequent Git operations execute.

**Uncertainty note:** I could not execute the actual Git-level exploit chain (that depends on OS-specific case-insensitivity/symlink behavior and the precise Git version bundled with this build), so the concrete end-to-end RCE could not be verified in this environment — the finding is based on the fact that a known, named upstream Git safety mechanism is explicitly and unconditionally disabled in this code path with no compensating check. A background Devin session with local Git tooling could reproduce the exploit chain end-to-end to confirm severity.

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

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
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

**File:** app/src/lib/git/rev-parse.ts (L56-63)
```typescript

    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }
```
