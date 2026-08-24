### Title
Git's recursive-clone RCE protection is explicitly disabled by GitHub Desktop, allowing a malicious remote to achieve code execution via a crafted submodule during clone - (File: `app/src/lib/git/clone.ts`)

### Summary
`Lender.sol` has a legitimate protection (the 3-day withdrawal delay) that is nullified because a second, unprotected code path (`approve`) reaches the same sensitive effect. The structural analog in GitHub Desktop is `clone()` in `app/src/lib/git/clone.ts`, which explicitly disables the environment guard that Git itself ships to prevent a known class of clone-time remote-code-execution (the class addressed by upstream Git's clone/submodule hardening, exposed via the `GIT_CLONE_PROTECTION_ACTIVE` toggle). Desktop hard-codes `GIT_CLONE_PROTECTION_ACTIVE: 'false'` while simultaneously passing `--recursive` to `git clone`, meaning the protection that should stop unsafe/symlinked/case-confusable submodule paths from being materialized is turned off for every single clone Desktop performs.

### Finding Description
`clone()` builds the execution environment for `git clone` like this: [1](#0-0) 

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

`--recursive` causes git to also clone every submodule referenced by the attacker-controlled remote's `.gitmodules`/tree during the same operation, without any prior user review of the submodule URLs or paths. Git's clone-time protections (the mechanism the `GIT_CLONE_PROTECTION_ACTIVE` variable governs) exist specifically to stop a malicious repository from placing files at unsafe locations relative to `.git` (e.g. via symlinked or case/Unicode-confusable submodule directory names) during this recursive materialization step, a technique previously used to smuggle a working hook/config file into a position where it executes automatically. By force-setting this guard to `'false'` on every clone call, Desktop is unconditionally opting every user out of that protection, for both the "Clone repository" flow and the "Clone Again" flow that use the same `clone()` function.

This mirrors the Lender.sol pattern precisely: the delay/guard exists in the underlying primitive (Git), but the wrapping application (Desktop, analogous to the "owner" contract) reaches the sensitive effect (materializing repository content on disk) through a path that intentionally bypasses the guard, and there is no equivalent compensating check anywhere else in the call chain — `_clone` / `CloningRepositoriesStore.clone` simply forward to this function.

### Impact Explanation
If Git's clone-time protection is the only thing standing between "attacker-controlled repository content" and "arbitrary file placement / hook execution on the victim's machine," then disabling it converts an ordinary `git clone` of a hostile or compromised repository/URL into a potential local file write or code-execution primitive, entirely outside the intended repository directory or with attacker-chosen content executed automatically. Since Desktop calls `clone()` for every user-initiated clone (including cloning a link/deep-link-supplied URL and "Clone Again" of a previously known repository), any user who clones a malicious repo is exposed — satisfying the "attacker controls a cloned/fetched repository" and "code execution / file write outside the repo" impact criteria.

### Likelihood Explanation
Likelihood is high for the trigger condition (any user clone of an attacker-supplied repo/URL, including via `x-github-client://` style deep links that route into `openOrCloneRepository`), because the disabling of the protection is unconditional and not gated by any settings, prompts, or trust state — unlike the `isRepositoryUnsafe`/`addSafeDirectory` flow used for adding *existing* local repositories, cloning goes straight through `clone()` with the protection turned off, so there is no user-facing warning or opt-in step that could stop it.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Desktop clones with Git's built-in protections active by default. If a legitimate reason exists to disable it (e.g. compatibility with certain LFS/progress parsing), gate it behind an explicit, user-visible trust decision (the same pattern already used for `isRepositoryUnsafe` / `addSafeDirectory`) rather than disabling it unconditionally for every clone.

### Proof of Concept
1. Host a malicious Git repository whose `.gitmodules` references a submodule with a symlinked or case/Unicode-confusable path designed to escape the intended submodule directory (the class of payload Git's clone protections are meant to reject).
2. In GitHub Desktop, use "Clone repository" (or a deep link that calls `openOrCloneRepository`) to clone that URL.
3. Observe that `clone()` executes `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment: [1](#0-0) , meaning Git does not apply its clone-time submodule-path safety check that would otherwise abort or sanitize the crafted submodule.
4. Because the guard is off, the malicious submodule content is materialized at the attacker-chosen location during the recursive clone, before the user has any opportunity to inspect or trust the repository.

Note: I could not access this repository's commit history/blame in this session to confirm when or why this override was introduced, so I cannot state whether it was intentional (e.g., for a specific compatibility reason) or accidental; this should be verified against the corresponding upstream `git-clone` documentation and Desktop's git history before remediation.

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
