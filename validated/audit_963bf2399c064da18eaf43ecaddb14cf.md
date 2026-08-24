### Title
Git clone-time hook/RCE protection explicitly disabled during recursive clone of attacker-controlled repositories - ([File: app/src/lib/git/clone.ts])

### Summary
`clone()` always sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and passes `--recursive` to `git clone`, unconditionally, for every clone the user performs. `GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git added to gate the hardening it introduced against malicious repository/submodule layouts that can smuggle an executable hook (e.g. via crafted submodule names/case-folding/`.git` alias tricks combined with `--recursive`) into the local working copy during clone, leading to code execution as soon as the hook fires. By forcing this protection off, Desktop reintroduces that entire class of clone-time hook-injection risk for any URL a user clones — which is exactly the "attacker controls a cloned/fetched repository" primitive called out as in-scope in this task.

### Finding Description [1](#0-0) 
shows the clone options built for every invocation of `clone()`:

```ts
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

`GIT_CLONE_PROTECTION_ACTIVE` is not a Desktop-internal invention — it is the switch Git upstream shipped to guard clone-time protections that block crafted repository/submodule structures (mixed-case `.git`, symlinked hook paths, embedded `hooks` directories reachable via `--recursive` submodule init) from writing an executable hook that runs automatically right after clone. Explicitly forcing it to `'false'` disables that guard for every single clone Desktop performs, and it's combined with `--recursive`, which is precisely the flag combination the protection was designed to cover (recursive submodule clone is the vector that lets an attacker's submodule tree place a hook file where Git will execute it).

There is no counterbalancing mitigation elsewhere in the codebase: `isClonePathSensitive()` at [2](#0-1)  only blocks a small allow-list of destination directories (home dir root, `.ssh`, `.gnupg`, `.config`, `.gitconfig`); it does nothing to validate the *content* of the remote repository/submodules being cloned. The hook-hardening code in `app/src/lib/hooks/get-repo-hooks.ts` and `app/src/lib/hooks/hooks-proxy.ts` governs Desktop's own trampoline-mediated hook execution for hooks the user already has locally — it does not compensate for Git's own clone-time protections being turned off during the initial `git clone --recursive`.

The corrupted invariant: Git's assumption that "a hook found in a freshly cloned repository/submodule tree cannot silently exist and be auto-executed unless the user explicitly trusted that layout" is broken, because Desktop tells Git to skip the exact runtime check that enforces this assumption.

### Impact Explanation
If a remote repository contains a crafted submodule/`.git` structure that the disabled protection was meant to catch, cloning it through Desktop (`git clone --recursive` with `GIT_CLONE_PROTECTION_ACTIVE=false`) can result in a malicious hook file being written into a location where Git — or a subsequent Desktop-triggered git operation — executes it automatically, i.e., arbitrary code execution on the victim's machine triggered purely by cloning an attacker-supplied URL/repo. This is the strongest applicable category from the valid-impact list: "attacker controls a cloned/fetched repository ... and the result is code execution."

### Likelihood Explanation
The precondition is only that the user clones (or opens/fetches) a URL supplied or influenced by the attacker — a completely standard, unprivileged Desktop workflow (e.g., via "Clone repository," a deep link, or opening a URL from a PR/notification). No local access, admin rights, or pre-existing compromise is required. The setting is unconditional in code (`GIT_CLONE_PROTECTION_ACTIVE: 'false'` is applied on every call to `clone()`, not just in a fallback/legacy branch), so every Desktop clone operation runs with the protection off, maximizing exposure. The main uncertainty is the exact Git-version-specific exploit primitive the protection blocks (I could not directly verify Git's internal check logic from this codebase, only that Desktop explicitly disables the documented flag); confirming the concrete exploitable payload would require testing against a real Git binary/version matrix, which is outside what the indexed code can show.

### Recommendation
Do not set `GIT_CLONE_PROTECTION_ACTIVE: 'false'`. Let Git's clone-time protections run by default (or explicitly set the variable to `'true'`/leave it unset) for all `clone()` invocations, including recursive submodule clones. If this was disabled to work around a specific compatibility issue, that workaround should be scoped narrowly (e.g., only for verified-safe/first-party remotes) rather than applied globally to every user-initiated clone of an arbitrary URL, and should be paired with independent validation of submodule paths/names before recursive checkout.

### Proof of Concept
1. Attacker publishes a public Git repository whose `.gitmodules`/submodule tree is crafted with the case-folding/alias trick that Git's clone protection (`GIT_CLONE_PROTECTION_ACTIVE`) is designed to reject, positioning a hook script (e.g. `post-checkout`) to land in an executable hooks path during recursive submodule initialization.
2. Victim opens the malicious URL via GitHub Desktop's "Clone a Repository" dialog, a `x-github-client://openRepo` deep link, or a PR link that triggers `openOrCloneRepository`.
3. Desktop calls `clone(url, path, options)`, which runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` in the environment (`app/src/lib/git/clone.ts:81-93`).
4. Because the protection is disabled, Git performs the recursive submodule checkout without the crafted-layout check, allowing the malicious hook to be placed and (depending on the specific technique) executed either immediately or on the next git operation Desktop performs in that repository (e.g., the status refresh or commit flow that follows every clone).
5. Expected secure behavior: the clone should fail or the malicious submodule layout should be rejected/sanitized, exactly as it would if `GIT_CLONE_PROTECTION_ACTIVE` were left at its default value.

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
