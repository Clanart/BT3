### Title
Git's clone-time symlink/hook protection is unconditionally disabled for every clone, reopening file-write-outside-repo RCE on attacker-controlled repositories - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` in `app/src/lib/git/clone.ts` sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment for every single `git clone` invocation, with no conditional logic gating it. [1](#0-0) 

This environment variable controls Git's own internal safety checks (added upstream as a fix for the class of clone-time symlink/case-collision attacks, e.g. CVE-2024-32002, where a malicious repository can trick `git clone --recurse-submodules` into writing/executing files outside the intended working tree by exploiting case-insensitive or symlinked `.git` directories). Desktop explicitly turns this protection off on every clone, regardless of whether the URL is remote/untrusted or local/trusted.

### Finding Description
The report's bug class is "a check that looks like a security control but is neutralized/never actually enforced, permitting an attacker-controlled primitive to reach a destructive operation without the intended guard." In the `HoneyJarPortal` case the redundant `_from != _msgSender()` check gave the appearance of an ownership guard while doing nothing to prevent burning arbitrary NFTs.

The Desktop analog is structurally the same pattern applied to Git's own clone-time protections: `clone()` always merges `GIT_CLONE_PROTECTION_ACTIVE: 'false'` into the execution environment before running `git clone --recursive -- <url> <path>`: [2](#0-1) 

There is a separate, real guard in the same file — `isClonePathSensitive()`, which blocks cloning into sensitive directories such as `~/.ssh` or `~/.gnupg` [3](#0-2) 

but that guard only checks the *destination directory* the user chose; it does nothing to stop a malicious repository's contents (symlinked `.git`, case-colliding paths, crafted submodules) from writing or executing files elsewhere on disk during the clone/`--recursive` submodule checkout, which is exactly what Git's own `GIT_CLONE_PROTECTION_ACTIVE` mechanism is designed to prevent. By disabling that mechanism unconditionally, Desktop removes the one guard that actually defends against attacker-controlled repository content, while leaving in place a guard (`isClonePathSensitive`) that addresses a different, narrower problem — mirroring the original report's pattern of a present-but-ineffective check masking the absence of the real one.

### Impact Explanation
If exploitable, cloning an attacker-hosted/attacker-controlled remote repository (fully within the allowed threat model: "attacker controls a cloned/fetched repository") could allow writing or executing files outside the intended clone directory via the very attack classes upstream Git's clone protection was built to stop. That satisfies "file write or read outside the repo" and potentially "code execution" per the valid-impact criteria.

### Likelihood Explanation
Likelihood is high in the sense that the disabling is unconditional — every `Clone repository` action in Desktop (via UI, CLI `--cli-clone`, or `x-github-client://openRepo/...` deep link) goes through this same `clone()` function and always carries the disabled flag. The exploitability, however, depends on whether the underlying Git/OS combination is still susceptible to the specific symlink/case-collision techniques `GIT_CLONE_PROTECTION_ACTIVE` mitigates (this varies by platform/filesystem, e.g. case-insensitive HFS+/APFS/NTFS vs. case-sensitive ext4). I could not verify, within index limits, why this variable was set to `'false'` (no comment or accompanying justification was found in the surrounding code), so it's uncertain whether this is intentional (e.g. compensating for a Dugite/Git version quirk) or an oversight.

### Recommendation
- Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override, or scope it strictly to trusted/local operations (e.g. test fixtures) rather than to all remote clones.
- If there is a compatibility reason this flag must be disabled (e.g. older Git/Dugite builds not supporting the check), replace it with an explicit, narrower mitigation and document the reasoning in code.
- Audit `updateSubmodulesAfterOperation`'s `allowFileProtocol` flag (`protocol.file.allow=always` in `app/src/lib/git/checkout.ts` / `app/src/lib/git/submodule.ts`) to confirm it is never set to `true` for remote/untrusted clone or checkout paths, since re-enabling `file://` submodule protocol is the same class of "undo an upstream Git CVE fix" pattern. [4](#0-3) [5](#0-4) 

### Proof of Concept
Not independently reproduced — this requires validating against a specific Git version's clone-protection behavior and filesystem (case-insensitive/symlink-capable) to confirm the concrete write-outside-repo primitive, which is outside what static code inspection can confirm. The code evidence establishes that the guard is disabled unconditionally for every clone: [1](#0-0) 
A concrete PoC would involve constructing a malicious repository (e.g. with a submodule or tree entry designed to collide with `.git` on a case-insensitive filesystem) and cloning it through Desktop's `clone-url` CLI action or `openRepositoryFromUrl` deep-link flow, then checking whether files land outside the clone directory — this step could not be executed in this read-only investigation.

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

**File:** app/src/lib/git/checkout.ts (L126-141)
```typescript
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
