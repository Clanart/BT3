### Title
`GIT_CLONE_PROTECTION_ACTIVE=false` on `git clone --recursive` re-enables the CVE-2024-32002 submodule-clone RCE - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` builds the environment for `git clone --recursive` and explicitly forces `GIT_CLONE_PROTECTION_ACTIVE: 'false'` [1](#0-0) . `GIT_CLONE_PROTECTION_ACTIVE` is the upstream Git safety switch introduced to fix CVE-2024-32002/GHSA-8h77-4q2w-9c2r, where a hostile repository defines submodules whose paths collide with (or symlink into) the top-level `.git` directory on case-insensitive or symlink-friendly filesystems, so that recursive clone/checkout writes an attacker-supplied hook (e.g. `post-checkout`) into `.git/hooks` and it executes automatically. Desktop disables that exact protection for every recursive clone it performs, which is analogous to the audited Beefy report: the app trusts an attacker-influenced object (here, a cloned repository's submodule layout instead of `_beefyBooster`) and removes the one guard (`endorsed`/`GIT_CLONE_PROTECTION_ACTIVE`) that would have blocked the malicious path.

### Finding Description
The invariant that upstream Git enforces via `GIT_CLONE_PROTECTION_ACTIVE=true` (the default since the fix) is: *a submodule's worktree/gitdir must never alias the parent repository's `.git` directory*. Desktop's `clone()` function unconditionally overrides this to `'false'` before invoking `git -c init.defaultBranch=... clone --recursive -- <url> <path>` [2](#0-1) [3](#0-2) . The only other safeguard present, `isClonePathSensitive()`, only checks the destination directory against a hardcoded list of sensitive local paths (`~/.ssh`, `~/.gnupg`, `~/.config`, etc.) [4](#0-3) ; it never inspects the cloned repository's own submodule definitions, so it does nothing to stop the aliasing attack that `GIT_CLONE_PROTECTION_ACTIVE` was designed to catch. Because `--recursive` is always passed for the initial clone, and `updateSubmodulesAfterOperation` also runs `submodule update --init --recursive` after every checkout/pull with the same lack of aliasing checks [5](#0-4) , a malicious repository author fully controls the content that triggers the vulnerable path each time a victim opens or updates the repo in Desktop.

### Impact Explanation
If an attacker publishes/links a Git repository whose `.gitmodules`/submodule paths are crafted to collide with `.git` on the victim's filesystem (case-insensitive HFS+/APFS/NTFS, or via clever path components), a victim who clones it with GitHub Desktop's "Clone repository" feature gets arbitrary hook script placement inside their own `.git/hooks`, which Git will execute on subsequent operations (checkout, commit, etc.) — i.e., attacker-controlled code execution on the victim's machine. This satisfies the "attacker controls a cloned/fetched repository ... result is code execution" impact class.

### Likelihood Explanation
Likelihood is high for the intended attacker model: no local/physical access, no admin rights, and no unnatural steps are required — the victim only has to use Desktop's normal clone flow against a repository they were pointed to (a very common phishing/typosquat vector for a Git client). The vulnerable code path (`clone()`) runs on every single clone performed through the app, and the disabling of the protection is unconditional, not feature-flagged or scoped to a legitimate compatibility need documented in the code.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override (or set it to `'true'`/omit it so Git's default protection applies) in `app/src/lib/git/clone.ts`, and apply the same review to `submodule.ts`'s recursive update calls. If protection was disabled to work around a specific legitimate scenario, that scenario should be handled by a narrow, explicitly justified exception rather than a blanket disable for all clones, and should be paired with the destination-side validation `isClonePathSensitive()` already provides.

### Proof of Concept
1. Attacker creates a repository containing a submodule entry in `.gitmodules` whose path, when combined with the top-level repo, aliases `.git` on a case-insensitive filesystem (the same construction as the public CVE-2024-32002 PoC), with the submodule's `.git`/hook content set to a malicious `post-checkout` (or similar) script.
2. Attacker shares the clone URL (e.g., via a GitHub link, `x-github-client://`/`github-mac://` deep link, or plain URL) and the victim clones it in GitHub Desktop, which calls `clone(url, path, options)` in `app/src/lib/git/clone.ts`.
3. Because Desktop sets `GIT_CLONE_PROTECTION_ACTIVE=false` in the spawned Git process's environment [1](#0-0) , Git's built-in aliasing check is skipped during the `--recursive` clone [6](#0-5) , and the malicious submodule content is written into the victim's real `.git` directory.
4. The planted hook executes on the next Git operation Desktop or the user performs in that repository, achieving code execution on the victim's machine.

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

**File:** app/src/lib/git/clone.ts (L86-93)
```typescript
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

**File:** app/src/lib/git/submodule.ts (L29-55)
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

  if (!progressCallback) {
    await git(args, repository.path, 'updateSubmodules', opts)
    return
```
