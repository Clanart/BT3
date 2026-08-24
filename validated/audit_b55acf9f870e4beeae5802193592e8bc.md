### Title
`git clone --recursive` explicitly disables Git's built-in clone protection (`GIT_CLONE_PROTECTION_ACTIVE=false`), re-enabling submodule symlink/`.git` directory-confusion RCE - (File: `app/src/lib/git/clone.ts`)

### Summary
The report's underlying pattern is: a safety invariant that the surrounding system is supposed to enforce (coin denomination matching before redelegating) is deliberately skipped, letting attacker-influenced input pass unchecked into a sensitive operation. The Desktop analog is in `clone()`: Git ships a built-in protection mechanism against maliciously crafted repositories (case-insensitive/symlinked `.git`, `.gitmodules`, or nested-submodule collision attacks) that Desktop explicitly disables for every `--recursive` clone by setting `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the child-process environment.

### Finding Description
`clone()` builds the environment for the `git clone --recursive` invocation and unconditionally sets: [1](#0-0) 
Git's clone/checkout protection (the same class of mitigation shipped for CVE-2024-32002-style submodule attacks) is meant to abort a clone when the fetched repository tree contains directory entries that would be treated as `.git` on case-insensitive or short-name-mangling filesystems, or nested symlinked submodule paths that let files be written outside the intended working directory. By forcing this environment variable to `'false'`, Desktop tells Git to skip that check for every recursive clone, regardless of platform. The clone destination check `isClonePathSensitive()` only validates the top-level target path against a fixed deny-list (home dir, `.ssh`, `.gnupg`, etc.) and does nothing to validate the *contents* written by a malicious remote during a recursive submodule checkout, so it does not compensate for the disabled protection. [2](#0-1) 
The command itself passes `--recursive` and the raw attacker-controlled `url`/`path` straight to `git`: [3](#0-2) 
No other guard in the clone path (proxy/auth env setup in `environment.ts`, `envForRemoteOperation`) inspects submodule content or filesystem entries created by the clone. [4](#0-3) 

### Impact Explanation
This falls squarely in the valid-impact category: the attacker controls a cloned repository (a malicious public repo, fork, or submodule target that a user clones through Desktop's normal "Clone repository" flow). With Git's clone-time protection disabled, a crafted repository/submodule tree can cause files to be written outside the intended repository directory (e.g. overwriting the real `.git/hooks` or files elsewhere on disk on a case-insensitive filesystem such as default macOS/Windows installs), which can lead to arbitrary code execution once Git or another tool later touches that corrupted `.git` state.

### Likelihood Explanation
Likelihood is high for any user who clones a hostile repository through Desktop's UI (a completely ordinary, unprivileged action — no local access, no admin rights, and no unnatural steps required). Desktop always runs recursive clones this way; the protection is disabled unconditionally rather than only when explicitly opted out by an advanced/trusted workflow, so every clone through the GUI is affected on the vulnerable filesystem configurations Git's protection was designed to guard against.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Git's native clone protection remains active for `git clone --recursive`, or replace it with a check that only disables the protection in a controlled, whitelisted context (never by default for arbitrary remote URLs). If disabling it was done to work around a specific dugite/Git-version incompatibility, that should be scoped narrowly (e.g., only for specific Git versions/environments) rather than applied globally to all clones.

### Proof of Concept
1. Host a malicious repository containing a submodule crafted to exploit the case-insensitive/symlink `.git` collision (the same technique addressed by upstream Git's clone protection, e.g. CVE-2024-32002).
2. Send the victim the clone URL (or have them fork/clone it via GitHub Desktop's "Clone repository" dialog).
3. Desktop calls `clone(url, path, options)` in `app/src/lib/git/clone.ts`, which sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>`.
4. Because the protection is disabled, Git proceeds with the recursive submodule checkout without validating the dangerous filesystem entries, allowing the malicious repository's submodule payload to write files outside the intended `path`, corrupting or replacing hook scripts/config that will later execute in the victim's environment.

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

**File:** app/src/lib/git/clone.ts (L81-84)
```typescript
  const env = {
    ...(await envForRemoteOperation(url)),
    GIT_CLONE_PROTECTION_ACTIVE: 'false',
  }
```

**File:** app/src/lib/git/clone.ts (L88-125)
```typescript
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

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}
```
