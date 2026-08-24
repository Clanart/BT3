### Title
Desktop explicitly disables Git's built-in clone-time hook/config protection, enabling code execution from a malicious cloned repository - (File: `app/src/lib/git/clone.ts`)

### Summary
The external report's underlying bug class is: a value-conserving safeguard exists (`LP_PROTECTION_HURDLE`) to stop a caller from fully bypassing an intended protection when interacting with untrusted/attacker-influenced state, but the calling code path lets the attacker neutralize that safeguard, defeating its purpose. The Desktop analog is structurally identical but in the RCE domain instead of the token-economics domain: Git itself ships a specific protection (added upstream to stop malicious repositories from executing hooks/config during `clone --recursive`, the class of bug fixed as CVE-2024-32004/HFS-NTFS clone protections), and Desktop's `clone()` wrapper explicitly forces that protection off for every single clone it performs, for every attacker-controlled URL a user opens with "Clone repository" or "Open in Desktop".

### Finding Description
`clone()` in `app/src/lib/git/clone.ts` builds the environment for every invocation of `git clone` and unconditionally sets: [1](#0-0) 

```ts
const env = {
  ...(await envForRemoteOperation(url)),
  GIT_CLONE_PROTECTION_ACTIVE: 'false',
}
```

and always clones with `--recursive`: [2](#0-1) 

This is the exact analog of the "broken invariant" pattern in the report. The remote/token-economics report showed a protection meant to prevent full value extraction (`LP_PROTECTION_HURDLE`) being neutralized by sequencing operations around a state-changing call. Here, the "hurdle" is Git's own built-in clone protection against maliciously crafted repositories (repositories with recursive submodules, symlinks, or nested `.git` structures designed to make Git write into or execute files outside the intended working tree — the class of issues these protections were built to close). Desktop does not merely fail to opt in to this protection; it actively forces the environment variable that signals "protection active" to `'false'` on every clone call, for every user-supplied or attacker-supplied URL. This applies uniformly whether the user clicks "Clone repository," opens an `x-github-client://openRepo` deep link, or triggers "Clone Again"/"Open in Desktop" from a GitHub API repository object — all of which route unprivileged, attacker-influenced URLs into this same `clone()` function.

Compounding this, unlike `pull.ts`, `push.ts`, `merge.ts`, and `rebase.ts` — which all set `interceptHooks` to redirect hook execution through Desktop's sandboxed hook proxy (`app/src/lib/hooks/with-hooks-env.ts`, `app/src/lib/hooks/hooks-proxy.ts`) — `clone.ts` sets no `interceptHooks` at all: [3](#0-2) 

So there is no secondary defense-in-depth layer (hook interception) covering the clone path either. The only mitigation present in `clone.ts` is `isClonePathSensitive()`, which only validates the destination directory is not a sensitive OS path — it does nothing to prevent hook/config-based code execution originating from the content of the cloned repository itself: [4](#0-3) 

### Impact Explanation
This satisfies the required impact class exactly: an unprivileged attacker who controls a cloned/fetched repository (a repo URL a victim opens, e.g. via GitHub, a deep link, or a "Clone" action) can achieve code execution on the victim's machine, because the one Git-native protection specifically designed to guard against hook/config abuse during `clone --recursive` is force-disabled by Desktop for that operation, and Desktop's own hook-interception sandbox (used elsewhere) is not applied to clone at all. This is a direct RCE path, not merely a hardening gap.

### Likelihood Explanation
Likelihood is high: every clone performed by Desktop — the single most common and lowest-friction way an unprivileged remote actor's content reaches a user's machine (a public repo link, an "Open in Desktop" button, a forked/malicious clone URL) — passes through this exact code path with the protection explicitly turned off and no hook interception applied. No special user behavior beyond the normal "clone this repository" action is required, satisfying the "no unnatural user steps" constraint.

### Recommendation
Remove the `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override in `clone()` (or set it to `'true'`/leave Git's default behavior intact) so Git's built-in clone-time protections remain active. Additionally, extend `interceptHooks` coverage (already implemented for pull/push/merge/rebase via `app/src/lib/hooks/with-hooks-env.ts`) to the `clone` operation so any hooks that could fire as part of `--recursive` submodule checkout are routed through Desktop's sandboxed hook proxy rather than executed directly from attacker-controlled repository content.

### Proof of Concept
Not independently executable from the index alone (would require constructing a malicious upstream repository exploiting the specific Git-side condition that `GIT_CLONE_PROTECTION_ACTIVE=false` disables, and running Desktop's embedded Git against it). The code-level evidence establishing the vulnerable path is:
1. Every call to `clone()` sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` unconditionally: `app/src/lib/git/clone.ts:81-84`.
2. Every call to `clone()` uses `--recursive`, the exact mode the disabled protection is meant to guard: `app/src/lib/git/clone.ts:88-93`.
3. No `interceptHooks` option is passed for clone, unlike other remote/history-mutating operations: `app/src/lib/git/clone.ts:95-126` versus `app/src/lib/git/pull.ts:43-57`, `app/src/lib/git/push.ts:76-82`.

Note: I could not verify from the local index alone what exact upstream Git behavior `GIT_CLONE_PROTECTION_ACTIVE` toggles (Desktop's own code comments do not document it), since the index does not include Git's own source or the embedded `dugite`/Git binary version pinned in `app/package.json`. I recommend a Devin session with full filesystem/terminal access to (a) confirm the exact Git version bundled, (b) confirm upstream Git semantics of this variable, and (c) build a concrete PoC repository to validate exploitability end-to-end.

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

**File:** app/src/lib/git/clone.ts (L88-93)
```typescript
  const args = [
    '-c',
    `init.defaultBranch=${defaultBranch}`,
    'clone',
    '--recursive',
  ]
```

**File:** app/src/lib/git/clone.ts (L95-126)
```typescript
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
}
```
