### Title
Permanent, path-scoped `safe.directory` trust exception is never re-validated against the repository's actual identity - ([File: app/src/lib/git/config.ts])

### Summary
The audited Solidity bug is a class of "unchallengeable genesis trust": the first element of a chain is accepted once, is never re-verified afterward, and its trust status persists even when the surrounding state (a batch of state roots) changes. GitHub Desktop has a structurally identical pattern in how it handles Git's "dubious ownership" (unsafe repository) protection: the trust decision is recorded permanently, keyed only by filesystem path, and is never re-checked against what is actually occupying that path afterward.

### Finding Description
When Desktop detects that a directory is a Git repository owned by a different user (`kind: 'unsafe'`, detected via `getRepositoryType` in `app/src/lib/git/rev-parse.ts:57-63`, which parses Git's "detected dubious ownership" error), it offers the user an "add an exception" action.

That action calls `addSafeDirectory(path)`: [1](#0-0) 

`addSafeDirectory` writes the literal path string into the **global** git config's `safe.directory` multi-value list via `addGlobalConfigValueIfMissing`. This exception:
- Is keyed only by the path string, not by repository identity (no binding to the initial commit SHA, remote URL, or any content hash).
- Is global (applies to every local git invocation and every Desktop-managed repository at that path), not repository-scoped.
- Has no expiry and no UI to revoke it once granted (`app/src/ui/add-repository/add-existing-repository.tsx:69-77` and `app/src/ui/missing-repository.tsx:35-50` both call `addSafeDirectory` and never provide a "forget this exception" path).

This mirrors the OVM bug exactly: the *first* trust decision at a given path becomes a permanent "genesis" fact that is never re-challenged, even though the thing actually occupying that path can change afterward (a directory being deleted and recreated, a network/synced share being repointed to different content, a shared CI/build agent reusing the same checkout path with a different account's content, etc.). Just as Optimism's `initializeFraudVerification` cannot prove fraud against the very first state root because there is no earlier state to compare against, Desktop's `getRepositoryType` cannot re-detect "unsafe" ownership at a previously-trusted path because Git's own dubious-ownership check is unconditionally short-circuited by the `safe.directory` allow-list — the check that would "challenge" a new, different owner/content at that path is permanently disabled the moment the exception is written.

### Impact Explanation
Git's dubious-ownership check exists specifically to stop automatic execution of attacker-supplied, repository-local configuration and hooks (`core.fsmonitor`, `core.pager`, `includeIf`, hook scripts, etc.) when a repository directory is not owned/controlled by the current user — the Desktop UI's own warning text states this plainly: "Adding untrusted repositories may automatically execute files in the repository" (`app/src/ui/add-repository/add-existing-repository.tsx:154-157`). Once a path is granted the exception, any future content that comes to occupy that exact path — regardless of who wrote it or when — silently regains that trust and can execute repository-configured code the next time Desktop or `git` operates on it, with no further prompt.

### Likelihood Explanation
This requires a scenario where the same path is legitimately trusted once and later comes to contain different/attacker-controlled content without the user re-adding the repository from scratch under a different path — for example, a shared network drive, a cloud-synced folder, or a reused CI/build checkout directory whose ownership metadata changes between uses. This is a narrower trigger than a purely remote/clone-based attack, so likelihood is lower than a directly reachable clone/fetch primitive, but it is not merely local-device compromise: the "genesis" flaw is that Desktop's trust model has no mechanism to re-validate an established exception against anything about the repository's actual provenance.

### Recommendation
Do not add a bare, permanent, global `safe.directory` entry on user consent alone. Consider scoping the exception per-session or re-validating ownership/ identity (e.g., comparing the initial trusted commit/remote against the current one) each time Desktop operates on a previously-"trusted" path, and provide a way to revoke exceptions from the UI.

### Proof of Concept
1. User adds a repository at path `P` that is initially flagged `kind: 'unsafe'` by `getRepositoryType`.
2. User clicks "Trust Repository", triggering `addSafeDirectory(P)`, which appends `P` to the global `safe.directory` list permanently.
3. At a later time, the content at path `P` changes to attacker-controlled data (e.g., a different repository is synced/mounted at the same path via a network share, or a shared machine/CI runner reuses `P` for a different checkout with attacker-supplied `.git/config`, hooks, or `core.fsmonitor`).
4. Desktop/`git` operations against `P` never re-run the ownership challenge — `safe.directory` unconditionally suppresses it — so the attacker-controlled repository-local configuration/hooks execute without any new warning or consent, exactly as if it were still the originally-trusted repository. [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/git/config.ts (L176-189)
```typescript
/**
 * Adds a path to the `safe.directories` configuration variable if it's not
 * already present. Adding a path to `safe.directory` will cause Git to ignore
 * if the path is owner by a different user than the current.
 */
export async function addSafeDirectory(path: string) {
  // UNC-paths on Windows need to be prefixed with `%(prefix)/`, see
  // https://github.com/git-for-windows/git/commit/e394a16023cbb62784e380f70ad8a833fb960d68
  if (__WIN32__ && path[0] === '/') {
    path = `%(prefix)/${path}`
  }

  await addGlobalConfigValueIfMissing('safe.directory', path)
}
```

**File:** app/src/lib/git/rev-parse.ts (L57-63)
```typescript
    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }
```
