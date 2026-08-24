Based on the evidence gathered, `app/src/lib/git/clone.ts` explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` when performing `git clone --recursive` on an attacker-supplied `url`.

### Title
Disabled clone protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) during recursive clone of untrusted repositories - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` builds the environment for every `git clone` invocation with `GIT_CLONE_PROTECTION_ACTIVE: 'false'` hard-coded [1](#0-0) , and always passes `--recursive` [2](#0-1) . `GIT_CLONE_PROTECTION_ACTIVE` is the internal flag Git itself uses to guard against symlinked/embedded `.git` directories and malicious submodule layouts encountered during a recursive clone (the class of bug fixed upstream by Git's clone-time submodule/hooks-path protections, e.g. CVE-2024-32004/CVE-2022-39253-style issues). Explicitly forcing this to `'false'` means Desktop asks Git to skip that protection for every clone URL a user enters or that arrives via a deep link/CLI action, i.e. fully attacker-controlled input.

### Finding Description
The `clone()` function is the single code path Desktop uses to clone repositories, whether triggered from the Clone dialog, `x-github-client://openRepo` deep links (`parseAppURL` → `dispatcher.openOrCloneRepository` → `clone()`), or the `github clone` CLI helper. It runs `git clone --recursive -- <url> <path>` with an environment that overrides Git's own safety flag: `GIT_CLONE_PROTECTION_ACTIVE: 'false'` [3](#0-2) . Because `--recursive` is always included, any submodules declared by the attacker-controlled remote (including submodules using `file://` URLs, since `protocol.file.allow` handling for submodules is only tightened at checkout-time in `checkoutBranch`/`updateSubmodulesAfterOperation`, not at initial clone) are fetched and checked out with the protection Git itself relies on to prevent unsafe worktree/`.git` layouts disabled. The comment/hardening pattern seen elsewhere in this codebase (`isClonePathSensitive`, `sanitizeCloneName`, `resolveWithin`) shows the team is actively defending against malicious-repository primitives, but this specific env var directly undoes a Git-side defense rather than adding one.

### Impact Explanation
If exploitable against the bundled Git version, a malicious repository (cloned via URL entered by the user, a deep link the user clicks, or the `github clone` CLI) could, during the initial recursive clone, write files outside the intended working directory or plant an executable hook that Desktop or a subsequent Git operation (which routes through `withHooksEnv`/`getRepoHooks`, itself designed to detect and proxy hooks) would run — turning "clone a repo" into arbitrary file write / code execution outside the repo. This matches the "attacker controls a cloned/fetched repository" impact category exactly.

### Likelihood Explanation
Likelihood cannot be confirmed with certainty from static analysis alone: whether this is actually exploitable depends on (a) the exact semantics of `GIT_CLONE_PROTECTION_ACTIVE` in the vendored Git/dugite version Desktop ships, and (b) whether upstream Git still performs the protection unconditionally regardless of this variable in the shipped version. I could not find the Git version pinned by dugite in the indexed files, nor any inline comment in `clone.ts` explaining why this protection is deliberately disabled (no matching comment was found, only the bare `env` assignment). That absence of justification, combined with it running against every user-supplied clone URL with `--recursive`, is what makes this stand out as a plausible regression from the safe-by-default hardening pattern used everywhere else in the codebase (`isClonePathSensitive`, `sanitizeCloneName`, `resolveWithin`).

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override (or set it to `'true'`/omit it to use Git's default) so Git's own clone-time protections remain active for `git clone --recursive` on attacker-supplied URLs. If there is a legitimate compatibility reason for disabling it (e.g. a known false-positive with a specific submodule layout Desktop needs to support), that justification should be documented inline and the override should be scoped as narrowly as possible rather than applied unconditionally to every clone.

### Proof of Concept
Not independently reproducible from the indexed code alone — reproduction would require: (1) confirming the exact Git version bundled via `dugite` and what `GIT_CLONE_PROTECTION_ACTIVE=false` disables in that version, and (2) crafting a malicious repository with a submodule/embedded-`.git` layout that this protection would normally block, then running `File > Clone Repository` in Desktop against that URL and observing whether it writes or executes content outside the target clone directory. I was not able to verify the Git/dugite version or execute this PoC within the available tools, so this should be validated with a live checkout of the Git version in use before treating it as confirmed.

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
