### Title
Recursive clone explicitly disables Git's `GIT_CLONE_PROTECTION_ACTIVE` symlink/hook-injection defense - (File: `app/src/lib/git/clone.ts`)

### Summary
Every clone performed by GitHub Desktop — including clones triggered from an attacker-controlled remote URL pasted by the user, an `x-github-client://openRepo/<url>` deep link, or a "Clone in Desktop"/PR checkout flow — is executed with `git clone --recursive` while forcibly setting the environment variable `GIT_CLONE_PROTECTION_ACTIVE=false` [1](#0-0) . This variable is Git's own internal guard (added upstream as part of the 2024 clone-time hook/symlink hardening fixes) that is supposed to remain active during a recursive clone so that Git can detect and refuse unsafe nested repository structures (e.g. a submodule directory replaced with a symlink, or an embedded `.git`/hooks path that would let a nested clone write or execute files outside the intended working tree). By hard-coding this to `'false'`, Desktop unconditionally disables that protection for both the top-level clone and every submodule clone/update performed as part of `--recursive`.

### Finding Description
`clone()` builds its `git` invocation with:
```ts
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = ['-c', `init.defaultBranch=${defaultBranch}`, 'clone', '--recursive']
...
args.push('--', url, path)
await git(args, __dirname, 'clone', opts)
``` [2](#0-1) 

`url` here is fully attacker-influenced when the clone is initiated through the app's custom protocol handler: `handleAppURL` → `parseAppURL` decodes the `openrepo` action and passes the embedded URL straight through to the clone flow [3](#0-2) [4](#0-3) , and `dispatchURLAction`/`openRepositoryFromUrl` ultimately clones it [5](#0-4) . Desktop's only clone-time hardening in this file is a destination-path allow/deny check (`isClonePathSensitive`) that blocks a handful of sensitive *destination* folders (home dir, `.ssh`, `.gnupg`, `.config`, `.gitconfig`) [6](#0-5) . That check says nothing about the *content* of the repository/submodules being cloned, and it does not compensate for disabling Git's own recursive-clone protection, which specifically defends against a malicious repository tree (not a malicious destination path) using symlinks/embedded git dirs in submodules to escape the intended clone directory or trigger hook execution during the recursive submodule init/update. `updateSubmodulesAfterOperation` similarly performs `submodule update --init --recursive` on attacker-supplied submodule URLs without the protection either [7](#0-6) .

Because Desktop explicitly forces the protection off rather than simply leaving Git's default (active) behavior in place, any hardening decision made by the underlying `dugite`/git binary in response to a malicious remote (crafted submodule paths, symlinked `.git`, hardlinked hooks, etc.) is neutralized for every clone/recursive-submodule operation the app performs.

### Impact Explanation
If the bundled Git version's clone-time symlink/hook-hardening (protected by `GIT_CLONE_PROTECTION_ACTIVE`) is what stands between "clone an attacker's repository" and "attacker escapes the destination tree / runs a hook binary from an untrusted path," then forcing this flag to `false` on every single clone removes that backstop app-wide. This is a file-write-outside-repo / potential code-execution primitive triggered purely by cloning a repository the attacker controls — reachable via a link the user clicks (`x-github-client://openRepo/...`), a URL pasted into "Clone repository," or automatic clone-from-PR/fork flows, none of which require local access, admin rights, or pre-existing malware.

### Likelihood Explanation
Likelihood is high for exposure (this code path runs on literally every clone and every recursive submodule update Desktop performs) but the ultimate severity depends on which specific CVE-class behavior `GIT_CLONE_PROTECTION_ACTIVE` gates in the exact dugite/git version Desktop ships; that runtime-version detail could not be confirmed from the indexed code (no changelog entry or comment in `clone.ts` explains why the flag is disabled). The existing `isClonePathSensitive` guard does not mitigate this because it only checks the destination path, not the repository content being cloned.

### Recommendation
- Do not force `GIT_CLONE_PROTECTION_ACTIVE=false`. Leave Git's default (active) protection enabled for both `git clone --recursive` and `git submodule update --recursive`, and only disable it if there is a concrete, documented compatibility reason — ideally scoped and re-enabled immediately after the specific known-safe operation, not applied blanket to every clone.
- If disabling is required for some legacy environment compatibility, gate it behind an explicit opt-in/allowlist rather than the default path used for arbitrary remote/attacker-supplied URLs.
- Add regression tests (mirroring the existing `test/unit/git/clone-test.ts` sensitive-path tests) that assert clone/submodule operations against a fixture repository containing symlinked submodule paths are rejected, not silently permitted.

### Proof of Concept
Conceptual PoC (cannot be executed from the indexed code alone — needs a live Desktop build + crafted remote):
1. Host a git repository containing a `.gitmodules` entry whose submodule path is later replaced by a symlink pointing outside the intended clone directory (the structure that `GIT_CLONE_PROTECTION_ACTIVE` is designed to catch during `git clone --recursive`/`git submodule update --recursive`).
2. Get a victim to open it via `x-github-client://openRepo/https://evil.example.com/attacker/repo` or paste the URL into Desktop's clone dialog.
3. Desktop calls `clone()` with `GIT_CLONE_PROTECTION_ACTIVE: 'false'` [1](#0-0) , so Git's own defense against this repository shape is skipped, while `isClonePathSensitive` only validates the destination folder chosen by the victim and does not inspect the cloned content [6](#0-5) .
4. Whatever write/execute-outside-repo behavior the upstream protection exists to prevent proceeds unguarded.

This is a code-derived analog based on the observed hard override of a Git safety environment variable; full exploit confirmation requires testing against the exact dugite/git version bundled with this build, which is outside what the indexed code can verify.

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

**File:** app/src/main-process/main.ts (L159-168)
```typescript
function handleAppURL(url: string) {
  log.info('Processing protocol url')
  const action = parseAppURL(url)
  onDidLoad(window => {
    // This manual focus call _shouldn't_ be necessary, but is for Chrome on
    // macOS. See https://github.com/desktop/desktop/issues/973.
    window.focus()
    window.sendURLAction(action)
  })
}
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/lib/git/submodule.ts (L45-55)
```typescript
  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
    return
```
