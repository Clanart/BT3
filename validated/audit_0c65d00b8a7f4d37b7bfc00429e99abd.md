## Title
Git clone hook-execution protection explicitly disabled during recursive clone/submodule init - (File: `app/src/lib/git/clone.ts`)

## Summary
GitHub Desktop's `clone()` function explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` while performing a `git clone --recursive` of an attacker-controlled URL. This environment variable is the kill-switch for Git's built-in `protection.clone.*` safety checks (the fix line for CVE-2024-32002 / CVE-2023-25652-class issues), which exist specifically to stop a malicious repository from smuggling hook scripts into `.git/hooks` via crafted (case-folded/symlinked) submodule paths during clone and having them executed automatically. By turning this protection off, Desktop reintroduces exactly the "external execution flow triggered mid-operation, before the operation is considered complete/trusted" pattern the original ERC1155 report warned about — except here the untrusted callback is a Git hook invoked during clone/submodule checkout on the user's machine, and the payload is attacker-controlled repository content instead of an attacker-controlled ERC1155 receiver contract.

## Finding Description
`clone()` builds the environment for the underlying `git` invocation like this: [1](#0-0) 

```
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
...
const args = [
  '-c', `init.defaultBranch=${defaultBranch}`,
  'clone',
  '--recursive',
]
```

`GIT_CLONE_PROTECTION_ACTIVE` controls Git's `protection.clone.*` hardening (added upstream to stop clones from placing files inside `.git/hooks`/`.git/modules` via crafted submodule names, case-insensitive collisions, or symlinked worktrees — the class of bug fixed as CVE‑2024‑32002). Setting it to `'false'` disables that check for every clone Desktop performs, and the clone is run with `--recursive`, which is precisely the flag that drives the vulnerable submodule-initialization code path the protection guards.

This is the same broken invariant as the report: Git will process attacker-supplied repository objects (here, submodule trees/paths) and, as a side effect, execute a callback (a hook script) before the "mint" — i.e., before Desktop's clone operation is finished and the repository is registered/trusted — completes. In the ERC1155 case, the guard missing was a reentrancy lock around `_mint`; here the guard missing is Git's own `protection.clone` check, and Desktop actively opts out of it instead of merely lacking it.

`checkout.ts` (`checkoutBranch`/`checkoutCommit`) provides supporting context: unlike `commit.ts`, `merge.ts`, `pull.ts` and `push.ts`, it does not pass `interceptHooks`, so any hook (e.g. `post-checkout`, which is in Desktop's own `knownHooks` list) triggered while checking out attacker-controlled refs runs outside the hooks-proxy sandbox in `hooks-proxy.ts` that normally strips `GIT_CONFIG_PARAMETERS`/`GIT_ASKPASS`/trampoline tokens from hook environments: [2](#0-1) [3](#0-2) 

## Impact Explanation
If a hook (or hook-equivalent file, e.g. via a malicious submodule path) is written into `.git/hooks` as a side effect of a protected-clone bypass, it executes with the local user's privileges as soon as Git's normal hook-invocation points fire (post-checkout, submodule update, etc.), which happens automatically as part of `clone --recursive`/`checkoutBranch`. This is arbitrary code execution triggered purely by the user cloning or opening a URL/repository they were led to (matches the "attacker controls a cloned/fetched repository... code execution" impact category). It requires no admin rights, no pre-existing malware, and no unnatural steps beyond cloning a repository Desktop is designed to let users clone (including via the `x-github-client://openRepo/...` deep link handled in `parse-app-url.ts` and `dispatcher.ts`'s `openOrCloneRepository`/`openBranchNameFromUrl`).

## Likelihood Explanation
Likelihood is high for the "protection disabled" fact itself (directly evidenced in code) but depends on whether the currently bundled Git version still contains code paths gated by `protection.clone.*`/`GIT_CLONE_PROTECTION_ACTIVE` that would otherwise block hook smuggling (this depends on the exact Git version vendored, filesystem case-sensitivity, and platform — Windows/macOS default case-insensitive filesystems are the primary at-risk targets for this class of bug). Regardless of whether a fully weaponized case-folding submodule payload is confirmed against the currently bundled Git, disabling a security control that Git upstream ships specifically to stop this bug class, for every clone Desktop performs, is a concrete, reachable regression with no compensating control elsewhere in the clone path.

## Recommendation
- Remove `GIT_CLONE_PROTECTION_ACTIVE: 'false'` from `app/src/lib/git/clone.ts` and instead determine why the protection was disabled (if it's causing false-positive clone failures for legitimate repos) and fix that root cause rather than disabling the guard outright.
- If disabling is unavoidable for some workaround, scope it narrowly and only after independently validating there are no case-fold/symlink collisions in submodule paths.
- Route `checkoutBranch`/`checkoutCommit` through the same `interceptHooks`/hooks-proxy sandbox used by `commit`/`merge`/`pull`/`push` so hooks fired during checkout of untrusted refs never see `GIT_CONFIG_PARAMETERS` (credential.helper=desktop), `DESKTOP_PORT`, or `DESKTOP_TRAMPOLINE_TOKEN`.

## Proof of Concept
1. An attacker crafts a Git repository containing a submodule entry whose path is chosen to collide (via case-folding, e.g. on Windows/macOS default filesystems) with `.git/hooks/post-checkout` or similar, following the CVE‑2024‑32002 technique, so that submodule initialization writes an executable script into the hooks directory.
2. Attacker distributes a clone URL or GitHub Desktop deep link (`x-github-client://openRepo/<url>`), or simply gets the victim to use "Clone repository" in Desktop.
3. Desktop calls `clone(url, path, options)` in `app/src/lib/git/clone.ts`, which runs `git -c init.defaultBranch=... clone --recursive ... url path` with `GIT_CLONE_PROTECTION_ACTIVE=false`, disabling the check that would otherwise abort the clone.
4. Submodule initialization (or a subsequent `checkoutBranch`/`checkoutCommit`, which also lacks `interceptHooks`) triggers the smuggled hook, executing attacker code on the victim's machine with the victim's privileges.

Note: full confirmation that the currently vendored Git binary is still susceptible to the underlying case-fold/symlink submodule bug (i.e., that the disabled `protection.clone` check is the only thing standing between attacker-controlled repo content and hook execution) would require testing against the specific `dugite`/Git version bundled with this build, which was not verified in this session.

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

**File:** app/src/lib/hooks/hooks-proxy.ts (L31-46)
```typescript
const excludedEnvVars: ReadonlySet<string> = new Set([
  // Dugite sets these, we don't want to leak them into the hook environment
  'GIT_SYSTEM_CONFIG',
  'GIT_EXEC_PATH',
  'GIT_TEMPLATE_DIR',
  // We set this to point to a custom hooks path which we don't want
  // leaking into the hook's environment. Initially I thought we would have
  // to sanitize this to strip out the custom config we set and leave any
  // user-configured but since we're executing the hook in a separate
  // shell with login it would just get re-initialized there anyway.
  'GIT_CONFIG_PARAMETERS',

  'GIT_ASKPASS',
  'GIT_SSH_COMMAND',
  'GIT_USER_AGENT',
])
```

**File:** app/src/lib/hooks/get-repo-hooks.ts (L10-20)
```typescript
const knownHooks = [
  'applypatch-msg',
  'pre-applypatch',
  'post-applypatch',
  'pre-commit',
  'pre-merge-commit',
  'prepare-commit-msg',
  'commit-msg',
  'post-commit',
  'pre-rebase',
  'post-checkout',
```
