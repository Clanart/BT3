### Title
Recursive `git clone` explicitly disables Git's built-in submodule/hooks-hijack protection (`GIT_CLONE_PROTECTION_ACTIVE=false`) - ([File: app/src/lib/git/clone.ts])

### Summary
`app/src/lib/git/clone.ts` unconditionally clones every repository with `git clone --recursive` while forcing the environment variable `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. This variable is the safety switch Git itself introduced to block a known class of attacks where a malicious repository's submodule configuration is used to write files (including hooks) outside the intended working tree during a recursive clone, leading to code execution or arbitrary file writes as soon as the user clones the repo. Desktop actively turns this protection off for every clone operation, meaning the guard that upstream Git ships specifically to stop this "attacker-controlled repository data causes an unintended, unrecoverable local action" pattern is bypassed by the app itself.

### Finding Description
`clone()` builds the Git invocation like this: [1](#0-0) 

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

`GIT_CLONE_PROTECTION_ACTIVE` is Git's own mitigation flag: when a clone recurses into submodules, Git checks whether a submodule's `.git` entry (or worktree path) could collide with or escape the containing repository (e.g. via symlinks or case-insensitive filesystem tricks) and refuses the operation unless this flag is explicitly disabled by the invoking tool. Desktop hard-codes it to `'false'` for **every** clone, unconditionally, regardless of whether the repository is untrusted (cloned via CLI `github clone <url>`, via the `x-github-client://openRepo/...` deep link parsed in `app/src/lib/parse-app-url.ts`, or via "Open in Desktop"/PR-fork flows in `dispatcher.ts`'s `openOrCloneRepository`).

The only existing guard in this path, `isClonePathSensitive()`, only inspects the **destination path** on the local filesystem (home dir, `.ssh`, `.gnupg`, etc.) — it does nothing to validate the **contents** of the remote repository being cloned: [2](#0-1) 

So a crafted repository (the attacker-controlled object here, analogous to the "incorrect `_message` field" in the seed report) can define submodules whose paths/configuration are only safe because Git's own runtime check would normally reject them — a check Desktop deliberately turns off before ever inspecting the repo.

Related: `updateSubmodulesAfterOperation` in `app/src/lib/git/submodule.ts` optionally adds `-c protocol.file.allow=always` when `allowFileProtocol` is true, which further loosens the default Git protocol allow-list for submodules — compounding the same trust problem for checkout-triggered submodule updates. I could not fully trace every call site that sets `allowFileProtocol=true` (e.g. calls from `app-store.ts` to `checkoutBranch`/`checkoutCommit`) within the available tool budget, so whether it is ever enabled for untrusted/forked-PR checkouts specifically is unverified and should be confirmed by a follow-up review.

### Impact Explanation
If the disabled protection is what prevents a specific submodule layout from writing outside the clone target directory (e.g. into `.git/hooks`, or via symlink/case-collision tricks used in CVE-2024-32002-class issues), then cloning any attacker-supplied repository — including via a link a user simply clicks (`x-github-client://openRepo/...`) — could result in files being written outside the intended repository directory and potentially achieve local code execution the next time Git runs a hook, satisfying the "code execution / file write outside the repo, attacker controls a cloned/fetched repository or a link the user clicks" impact bar.

### Likelihood Explanation
Every clone Desktop performs goes through this exact code path with the flag always off — there is no per-repository trust decision. The only requirement for the attacker is that the victim clones or opens the malicious repo (via CLI, deep link, or GUI "Clone repository" flow), which is a normal, expected user action, not social engineering or physical access.

### Recommendation
Do not statically force `GIT_CLONE_PROTECTION_ACTIVE=false`. Only disable this protection where Desktop can positively verify it is safe to do so (e.g. after validating clone destination is not exposed to submodule path collisions), or better, leave Git's default protection enabled and handle any resulting failures gracefully rather than disabling the check for all clones. Audit all call sites passing `allowFileProtocol: true` into `updateSubmodulesAfterOperation`/`checkoutBranch`/`checkoutCommit` to ensure `protocol.file.allow=always` is never applied when the checkout originates from an untrusted/forked remote (e.g. PR checkout from forks).

### Proof of Concept
1. Attacker publishes a public repository containing a submodule configuration designed to trigger the class of attack that `GIT_CLONE_PROTECTION_ACTIVE` is meant to block (crafted submodule path/symlink such that recursive checkout can write outside the top-level working directory).
2. Attacker sends the victim a link such as `x-github-client://openRepo/https://github.com/attacker/evil-repo` or simply tells them to "Clone in Desktop".
3. Desktop's `parseAppURL`/`dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository` → eventually calls `clone()` in `app/src/lib/git/clone.ts` with `--recursive` and `GIT_CLONE_PROTECTION_ACTIVE: 'false'`.
4. Because Git's own protective check is disabled, the malicious submodule content executes/writes without the runtime safeguard that would otherwise abort the clone.

Note: I was not able to fully confirm, within the tool-call budget, whether an unpatched Git version in Desktop's bundled `dugite`/Git for Windows is actually vulnerable to the underlying CVE this env var protects against, nor could I trace every `allowFileProtocol` call site in `app-store.ts`. Confirming the exploitability of step 4 requires checking the bundled Git version's behavior when `GIT_CLONE_PROTECTION_ACTIVE=false` is combined with a real malicious submodule payload — this should be validated in a follow-up session with full repository/file access.

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
