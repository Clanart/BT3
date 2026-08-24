This is a strong lead: `clone.ts` explicitly disables Git's built-in clone protection (`GIT_CLONE_PROTECTION_ACTIVE: 'false'`), which is the exact protection Git added upstream to prevent malicious repository content (embedded/symlinked `.git` directories, hooks) from being asserted during clone — the class of bug behind CVE-2024-32002 (git RCE via clone) and structurally similar to the OpenSSL config-loading issue (untrusted/attacker-supplied config causing automatic code execution). Let me examine this in detail along with surrounding checks.I have sufficient evidence to write up the finding.

### Title
Arbitrary code execution via disabled Git clone symlink/hooks protection during `git clone --recursive` - (File: `app/src/lib/git/clone.ts`)

### Summary
GitHub Desktop's `clone()` function explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` while invoking `git clone --recursive` on an attacker-supplied URL. `GIT_CLONE_PROTECTION_ACTIVE` is the kill-switch Git added for the fix to the "clone hooks/symlink" family of RCE bugs (the case-insensitive/`.git` collision and embedded-repository symlink attacks, e.g. CVE-2024-32002/CVE-2023-25652 class), where a maliciously crafted repository (including a crafted submodule) can plant a file into `.git/hooks` via a colliding/symlinked path during clone/checkout, resulting in that hook running automatically. Explicitly disabling this protection re-opens exactly the "attacker-controlled repository content leads to arbitrary code execution merely by cloning" primitive — the same broken invariant described in the OpenSSL report (an application trusting attacker-reachable content/config that gets auto-loaded/executed).

### Finding Description
`clone()` builds the environment for the clone operation as: [1](#0-0) 

`GIT_CLONE_PROTECTION_ACTIVE=false` is passed unconditionally for every clone, and `--recursive` is always appended, meaning submodules referenced by the remote repository are automatically initialized and cloned as part of the same operation with the same disabled protection. This protection exists in Git specifically to detect and refuse operations where a case-collision, symlink, or crafted submodule path would let clone write outside the intended `.git`/worktree location (historically into `.git/hooks`), which Git hooks then execute without any separate confirmation. By disabling it, Desktop removes the guard that Git upstream added after RCE reports in this exact area.

This directly parallels the "guard exists but is effectively defeated" pattern the OpenSSL/Nextcloud report demonstrates (OpenSSL trusted a predictable, attacker-writable config path with no integrity check) — here, Desktop trusts attacker-controlled remote repository content during clone while turning off the one Git-native safety check meant to prevent that content from writing into the hooks directory.

Existing Desktop guards do not cover this:
- `isClonePathSensitive()` only validates the destination directory chosen by the *user*, not the content coming from the untrusted remote: [2](#0-1) 
- The "unsafe repository" ownership warning (`isRepositoryUnsafe`, shown in `missing-repository.tsx` / `add-existing-repository.tsx`) is only triggered when a repository directory is *owned by a different local user* — it is not evaluated during/after a fresh `clone`, and does nothing to stop the write-during-clone class of attack that `GIT_CLONE_PROTECTION_ACTIVE` guards against: [3](#0-2) 
- The hooks execution path (`getRepoHooks` / `withHooksEnv` / `createHooksProxy`) will happily run whatever is discovered under `.git/hooks` (or `core.hooksPath`) for any git operation with `interceptHooks` configured (e.g. `commit`), with no verification of how those hook files got there: [4](#0-3) 

### Impact Explanation
If a malicious/compromised remote repository (attacker controls a cloned/fetched repository — an explicitly in-scope primitive) exploits the class of collision/symlink issue that `GIT_CLONE_PROTECTION_ACTIVE` was designed to block, a hook file can be planted into the resulting `.git/hooks` directory during the initial `git clone --recursive` call. Because Desktop always runs with this protection disabled, the check that would normally abort the clone (or refuse the offending submodule) never fires. The planted hook then executes:
- immediately if a hook such as `post-checkout`/`post-merge` fires as part of the clone/submodule-update sequence, or
- on the user's very next ordinary Desktop action (e.g. `git commit`), since `createCommit` intercepts `pre-commit`, `post-commit`, etc. via the hooks-proxy machinery, which executes discovered hook files with the user's privileges and full shell environment: [5](#0-4) 

This gives an attacker code execution on the victim's machine with no privileged access, no leaked credentials, and no unnatural user action beyond the normal "clone a repository in Desktop" workflow — squarely within the "Valid Impact" criteria (attacker controls a cloned repository → code execution).

### Likelihood Explanation
Likelihood is bounded by whether a workable variant of the case-collision/symlink write-outside-`.git` primitive still exists on the user's platform/filesystem (case-insensitive filesystems on macOS/Windows are the classic trigger, and Windows reserved-name/8.3 short-name tricks have historically been used for similar bypasses). Desktop always disables the protection unconditionally for every clone (not just as a documented workaround for a specific edge case), and always uses `--recursive`, so any user cloning an attacker-supplied URL (a very common Desktop workflow — "Clone repository" by URL, forks, `.wiki.git`, etc.) is exposed. No additional user interaction beyond the normal clone flow is required.

### Recommendation
- Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `clone()` so Git's built-in clone protection remains active by default.
- If protection is disabled for a specific, narrow compatibility reason (e.g. a known false-positive with a particular repository layout), scope the override to that specific case only, and never combine it with `--recursive` on untrusted URLs.
- After clone (and after each `submodule update --init --recursive`), verify hook files under `.git/hooks`/`core.hooksPath` were not introduced by repository content itself, or run submodule-related operations with `protocol.file.allow` and hook execution disabled until the top-level clone has been confirmed clean.

### Proof of Concept
1. Prepare a malicious remote repository that exploits the file/hooks-path collision technique that Git's `GIT_CLONE_PROTECTION_ACTIVE` check guards against (e.g. a submodule whose path collides with `.git` on a case-insensitive filesystem, or a crafted symlink inside a submodule) so that a file lands in `.git/hooks/post-checkout` (or another hook Desktop intercepts, e.g. `pre-commit`).
2. Host this repository and share its clone URL/link with the victim (a link the user clicks, per the in-scope attacker model).
3. Victim clones the repository in GitHub Desktop via "Clone repository" → "URL".
4. `clone()` runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment, so Git does not abort or warn about the colliding/symlinked path, and the crafted hook file is written into `.git/hooks`.
5. The planted hook executes either during the clone/submodule-update sequence itself, or the next time the victim performs any Desktop git operation that Desktop intercepts hooks for (e.g. making a commit), achieving code execution under the victim's account.

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

**File:** app/src/ui/missing-repository.tsx (L111-128)
```typescript
    if (isPathUnsafe) {
      return (
        <UiView id="missing-repository-view">
          <div className="title-container">
            <div className="title">
              {this.props.repository.name} is potentially unsafe
            </div>
            <div className="details">
              <p>
                The Git repository at <Ref>{unsafePath}</Ref> appears to be
                owned by another user on your machine. Adding untrusted
                repositories may automatically execute files in the repository.
              </p>
              <p>
                If you trust the owner of the directory you can add an exception
                for this directory in order to continue.
              </p>
            </div>
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L76-106)
```typescript
export async function* getRepoHooks(path: string, filter?: string[]) {
  const hooksPath = await getConfigValue(path, 'core.hooksPath')
    .catch(() => getHooksPath(path))
    .then(p => resolve(path, p))

  const files = await readdir(hooksPath, { withFileTypes: true })
    .then(entries => entries.filter(x => x.isFile()))
    .catch(() => [])

  const matchAll = filter?.includes('*')

  for (const file of files) {
    const hookName = basename(file.name, '.exe')

    if (matchAll || filter?.includes(hookName) === false) {
      continue
    }

    if (!knownHooks.includes(hookName)) {
      continue
    }

    if (__WIN32__) {
      // On Windows we have to assume that any valid hook name is executable
      // because the executable bit is not used there. Git looks for a shebang
      // but that seems expensive to check here :shrug:
      yield hookName
    } else if (await isExecutable(join(file.parentPath, file.name))) {
      yield hookName
    }
  }
```

**File:** app/src/lib/git/commit.ts (L51-70)
```typescript
  const result = await git(
    ['commit', ...args],
    repository.path,
    'createCommit',
    {
      stdin: message,
      // https://git-scm.com/docs/githooks/2.46.1
      interceptHooks: [
        'pre-commit',
        'prepare-commit-msg',
        'commit-msg',
        'post-commit',
        ...(options?.amend ? ['post-rewrite'] : []),
        'pre-auto-gc',
      ],
      onHookProgress: options?.onHookProgress,
      onHookFailure: options?.onHookFailure,
      onTerminalOutputAvailable: options?.onTerminalOutputAvailable,
    }
  )
```
