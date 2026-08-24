### Title
Git's post-CVE-2024-32004 clone-protection is explicitly disabled during recursive clone - ([File: app/src/lib/git/clone.ts])

### Summary
The external report's underlying bug class is a security-relevant variable that never gets set to its protective value, so a defense that should be active is silently a no-op. In GitHub Desktop's `clone()` function, the analogous flaw is not an *uninitialized* variable but a **deliberately hard-coded disabling** of Git's own clone-time protection flag, `GIT_CLONE_PROTECTION_ACTIVE`, which is unconditionally set to `'false'` for every clone Desktop performs, including recursive submodule clones of attacker-controlled URLs.

### Finding Description
`clone()` builds the environment for every `git clone` invocation and always includes: [1](#0-0) 

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = [
  '-c',
  `init.defaultBranch=${defaultBranch}`,
  'clone',
  '--recursive',
]
```

`GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git itself introduced (Git ≥2.45.1) as part of the fix for the clone-time hook/config-injection class of vulnerabilities (CVE-2024-32004 and related issues), where a maliciously crafted repository — via nested submodules, symlinked `.git` paths, or crafted `core.hooksPath`/`core.fsmonitor` config — could cause Git to execute attacker-controlled code as part of a `clone --recursive` before the user ever inspects the checked-out content. The upstream protection is meant to detect and refuse such configurations during the recursive submodule expansion.

By unconditionally forcing this variable to `'false'`, Desktop turns that Git-level guard off for every single clone operation, exactly mirroring the report's bug class: a protective mechanism exists in the underlying system, but the value that should enable it is never allowed to take its protective (enabled) state — here it's worse than "uninitialized," it is explicitly and permanently overridden to the unsafe value. Unlike the local-storage-backed feature flags elsewhere in the app (which correctly default to safe values, e.g. `useExternalCredentialHelperDefault = false`) [2](#0-1) , there is no code path, setting, or setter that lets this value be `'true'`.

The clone is also always performed with `--recursive`, meaning any malicious submodules referenced by the top-level repository are expanded automatically as part of the same operation this variable is meant to guard: [3](#0-2) .

### Impact Explanation
The attacker only needs to control the content of a repository that a Desktop user clones (a directly supported "unprivileged" primitive per the impact criteria: attacker-controlled cloned repository). If that repository (or one of its submodules, since `--recursive` is always used) is constructed to exploit the class of clone-time hook/config-injection bugs that `GIT_CLONE_PROTECTION_ACTIVE` was created to stop, Desktop's explicit `'false'` override removes the guard, potentially allowing code execution on the victim's machine at clone time — before the user has had any chance to inspect the repository contents. This lines up with the "impact" bar of code execution driven by a malicious cloned/fetched repository.

### Likelihood Explanation
Likelihood is high in the sense that the disabling is unconditional and always in effect (100% of clones go through this code path) — there is no configuration or user action that would re-enable the Git-side protection. The likelihood of an actual successful exploit depends on the underlying Git protection still catching a currently-unknown or unpatched variant of the CVE-2024-32004 class of clone-time attacks, since ordinary Git clone-time hook execution is already blocked by other mechanisms (e.g., `core.hooksPath` restrictions). However, the whole reason upstream Git ships this specific opt-in/opt-out flag is to have a last line of defense for exactly these recursive-submodule/hooks scenarios, and Desktop nullifies it for every operation.

### Recommendation
Do not set `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Either omit the variable entirely (allowing Git's own default, protective behavior to apply) or explicitly set it to `'true'` if Desktop needs to assert that it is operating in a protected/trusted context. If the override was added to work around a compatibility issue (e.g., false-positive protection triggers during Desktop's own internal recursive submodule handling), that workaround should be scoped as narrowly as possible and paired with equivalent path/config validation in Desktop's clone flow rather than blanket-disabling the upstream mitigation.

### Proof of Concept
1. Construct a malicious Git repository containing a submodule (or nested `.git` config) crafted to trigger the class of clone-time hook/config execution that `GIT_CLONE_PROTECTION_ACTIVE` is designed to block (per the upstream Git CVE-2024-32004 advisory reproduction steps).
2. Host the repository at a URL and have the victim clone it via GitHub Desktop's "Clone repository" flow (`clone()` in `app/src/lib/git/clone.ts`).
3. Because Desktop always injects `GIT_CLONE_PROTECTION_ACTIVE: 'false'` into the clone environment and always passes `--recursive`, the underlying Git protection check is bypassed during submodule expansion, and the payload executes as it would on an un-patched Git client, despite the user running a Git version that ships the fix. [1](#0-0)

### Citations

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

**File:** app/src/lib/trampoline/use-external-credential-helper.ts (L1-8)
```typescript
import { getBoolean, setBoolean } from '../local-storage'

export const useExternalCredentialHelperDefault = false
export const useExternalCredentialHelperKey: string =
  'useExternalCredentialHelper'

export const useExternalCredentialHelper = () =>
  getBoolean(useExternalCredentialHelperKey, useExternalCredentialHelperDefault)
```
