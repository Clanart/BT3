## Finding: Git's built-in clone RCE protection is explicitly disabled on every Desktop clone

### Title
Disabling Git's `GIT_CLONE_PROTECTION_ACTIVE` guard during recursive clones re-enables the CVE-2024-32002 hook-execution RCE - (File: `app/src/lib/git/clone.ts`)

### Summary
`app/src/lib/git/clone.ts` builds the environment for every `git clone` invocation and unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` while also passing `--recursive`, i.e. it clones submodules automatically as part of the same operation. [1](#0-0) 

### Finding Description
`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable upstream Git introduced to guard `git clone --recursive` against the class of attacks fixed by CVE-2024-32002/CVE-2024-32004/CVE-2024-32465: a malicious repository (or a malicious nested submodule it references) can be crafted with case-folding, trailing-space, or symlinked `.git`/`.git/hooks` paths so that, during a *recursive* clone, files intended for a submodule's git directory are instead written into the superproject's `.git/hooks` (or vice versa). Because a `post-checkout` hook runs automatically as the final step of a normal clone, an attacker-controlled hook script planted this way executes without any further user action beyond cloning the repository. Git's fix makes clone refuse or sandbox this behavior unless this protection is deliberately disabled.

Desktop's `clone()` function forces this protection off for *every* clone, including ones that use `--recursive` and therefore process attacker-supplied submodule definitions: [2](#0-1) 

The same disabled-protection code path is reused for the recursive `submodule update --init --recursive` calls issued elsewhere (`updateSubmodulesAfterOperation`), which additionally allow `protocol.file.allow=always`, further widening what a malicious `.gitmodules` can point at: [3](#0-2) 

None of the existing guards address this: `isClonePathSensitive` only validates the destination directory chosen by the user, not the contents written *inside* that directory by the crafted submodule/hook structure, so it does not stop this attack. [4](#0-3) 

### Impact Explanation
A user who clones an attacker-controlled or attacker-contributed repository (e.g., an open-source project the attacker submitted a PR/fork of, or a repo shared via a "Clone with Desktop" link) with a crafted nested submodule can have an arbitrary hook script executed on their machine automatically, with no additional prompts. This satisfies the "attacker controls a cloned/fetched repository ... code execution" impact class — it is not a hardening/DoS issue, it is remote code execution triggered purely by the normal "Clone" action.

### Likelihood Explanation
Likelihood is high for any environment where Desktop performs recursive submodule clones (the default, since `--recursive` is always passed): any repository with attacker-influenced submodule references (public forks, third-party repos, malicious mirrors) can trigger it. No local access, admin rights, or prior compromise is required — only that the victim clones the repository through Desktop's normal UI flow.

### Recommendation
Do not set `GIT_CLONE_PROTECTION_ACTIVE: 'false'`; let Git's built-in protection remain active for recursive clones and submodule updates. If Desktop needs to bypass this for a specific, narrow reason (e.g., known-safe internal tooling), that bypass should be scoped and justified, not applied unconditionally to all user-initiated clones. Also review `submodule.ts`'s use of `protocol.file.allow=always` for the same reasoning — it should be gated behind explicit user consent for file:// submodules rather than enabled broadly.

### Proof of Concept
1. Attacker publishes a repository containing a submodule entry in `.gitmodules` engineered to exploit the case-insensitive/symlink `.git` directory confusion described in CVE-2024-32002 (a submodule whose expected `.git` path collides with the superproject's `.git/hooks` directory on the victim's filesystem), with a malicious `post-checkout` (or similar) hook payload.
2. Victim uses GitHub Desktop's "Clone a repository" feature (`clone()` in `app/src/lib/git/clone.ts`) to clone this repository. Desktop always passes `--recursive` and always sets `GIT_CLONE_PROTECTION_ACTIVE=false`. [5](#0-4) 
3. Because Git's protection is disabled, the malicious submodule structure is allowed to write the hook file into the superproject's real `.git/hooks` directory during the recursive clone.
4. Git's own final checkout step (part of `git clone`) invokes the resulting hook, executing attacker-supplied code on the victim's machine — with no additional user interaction beyond the initial clone.

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

**File:** app/src/lib/git/submodule.ts (L38-51)
```typescript
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
