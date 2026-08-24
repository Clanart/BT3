## Title
`addSafeDirectory` grants permanent, path-based Git trust that is never re-validated against ownership/content at time of use - (File: `app/src/lib/git/config.ts`)

### Summary
The external report's core flaw is a **one-time, asynchronous trust-elevation primitive** (`importScore`) whose effect is written to persistent state and never re-checked against the *current* condition that justified it, letting an attacker exploit a stale, previously-earned trust value at a time of their choosing. The closest verifiable analog in this GitHub Desktop codebase is `addSafeDirectory` [1](#0-0) , which is invoked from `AddExistingRepository.onTrustDirectory` and `MissingRepository.onTrustDirectory` [2](#0-1) [3](#0-2) . Once a user clicks "Trust repository" for a path that Git flagged as "unsafe" (owned by a different user), Desktop appends that **path** (not a content hash, not an ownership record) to the global `safe.directory` git config forever. From then on `getRepositoryType` will treat *anything* later found at that same path as trusted [4](#0-3) , with no mechanism to revoke or re-validate the exception if the directory's ownership/content changes afterward.

### Finding Description
The broken invariant is: **"a directory that was safe when the user clicked Trust remains safe forever, regardless of what/who controls it afterward."** This mirrors `importScore`'s `m_workerScores[_worker].max(...)`, which permanently commits a snapshot of trust that is disconnected from the worker's *current* state.

Concretely:
- `addSafeDirectory(path)` writes `path` into the **global** `safe.directory` list via `addGlobalConfigValueIfMissing` [5](#0-4) . This is a per-machine, per-user setting that is not scoped to a specific git repository, commit, or ownership SID captured at grant time.
- Once whitelisted, `getRepositoryType(path)` will never again return `{ kind: 'unsafe' }` for that path — Git itself simply stops enforcing the ownership check for anything under it [6](#0-5) .
- Desktop's UI flows (`AddExistingRepository`, `MissingRepository`, `_relocateRepository`) all rely solely on `getRepositoryType`'s `kind` to decide whether to warn the user or silently proceed [7](#0-6) . There is no secondary check (e.g., re-verifying the directory owner SID/UID still matches who was present when trust was granted, or binding the exception to a specific git dir/commit).
- Because the check is purely path-based, any future content placed at that exact path — for example on a shared/mapped location, a re-imaged machine, a path reused after a previous repo was removed, or a path an attacker with non-admin write access to a shared parent directory can write into — inherits full trust with zero indication to the user that the underlying content changed. Desktop's own warning text explicitly says "Adding untrusted repositories **may automatically execute files in the repository**" [8](#0-7) , confirming that trusting a path is understood internally to gate code-execution-relevant behavior — yet the gate, once opened, never closes or re-validates.

### Impact Explanation
If an attacker can (re)populate the exact path a user previously trusted — e.g., a project directory on a network share, a CI-mounted volume, or a location later reused when a repo is removed and a new one is placed there by a different, non-owner actor — Desktop and the underlying Git executable will treat the new, attacker-supplied content as fully trusted without any prompt. This can be leveraged to trigger execution of repository-controlled content (build tooling, editor auto-run configuration, etc.) that Desktop's own unsafe-repository warning was specifically designed to gate. It silently defeats a security control the user believes is still protecting them, i.e., "silent corruption of what the user commits/pushes/executes" from a previously-trusted-but-now-attacker-controlled location.

### Likelihood Explanation
Likelihood is moderate rather than high: it requires that an attacker later gain write access to the specific path a user already trusted (e.g., shared drives, reused machine paths, redirected mount points), which is a narrower precondition than a fully remote, zero-interaction exploit. However it requires **no admin rights, no local code execution primitive, and no social engineering beyond the original one-time "Trust repository" click**, which is a normal, expected user action when adding a legitimate repository. Given the persistence is indefinite and machine/user-global (not tied to the originating repository or its git dir), the window of exposure is unbounded in time.

### Recommendation
- Do not persist trust purely by path. Bind the exception to something that changes when ownership/content changes (e.g., record the owner SID/UID and the resolved `.git` object at grant time, and re-validate both before treating the directory as trusted again), analogous to requiring the `importScore` recipient to still hold the identity that earned the original reputation.
- Provide a way to list/revoke previously trusted directories from Desktop's settings, and re-prompt if the directory's owner changes after the exception was granted.
- Consider scoping the exception to `--local`/per-repository configuration rather than `--global`, reducing blast radius if a path is reused for unrelated content.

### Proof of Concept
1. User adds/opens a repository at path `P` that Git reports as "unsafe" (different owner) and clicks "Trust repository" — Desktop calls `addSafeDirectory(P)`, adding `P` to the global `safe.directory` list [2](#0-1) .
2. User later removes the repository at `P` (e.g., via "Remove", or the directory is emptied/recreated on a shared drive).
3. An attacker who can write to `P` (e.g., another account on a shared network location, or a CI/automation actor with access to that shared mount) places a malicious Git repository at the same path `P`.
4. The victim reopens Desktop, or Desktop rescans repositories; `getRepositoryType(P)` calls Git, which finds `P` in `safe.directory` and returns `{ kind: 'regular', ... }` with no unsafe-ownership warning [4](#0-3) , even though the directory is now owned/controlled by someone other than the user, and even though its content is completely different from what was originally trusted.
5. Desktop treats the attacker's repository as fully trusted, with none of the protections the "unsafe" check was designed to enforce.

**Confidence note:** I could not find, within the indexed portion of this codebase, any mechanism that re-validates or expires `safe.directory` entries, nor any binding of the exception to ownership/content captured at grant time — only the one-time `addGlobalConfigValueIfMissing('safe.directory', path)` call. Full verification of Git's own behavior around `safe.directory` semantics (e.g., whether Git itself offers any finer-grained binding) is outside this repository's code and would need to be confirmed against the vendored/embedded Git version Desktop ships.

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

**File:** app/src/ui/missing-repository.tsx (L35-50)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingPath: true })
    const { unsafePath } = this.state
    const { repository } = this.props

    if (unsafePath) {
      await addSafeDirectory(unsafePath)
      const type = await getRepositoryType(repository.path)

      this.setState({ isTrustingPath: false })

      if (type.kind !== 'unsafe') {
        this.checkAgain()
      }
    }
  }
```

**File:** app/src/ui/missing-repository.tsx (L118-127)
```typescript
            <div className="details">
              <p>
                The Git repository at <Ref>{unsafePath}</Ref> appears to be
                owned by another user on your machine. Adding untrusted
                repositories may automatically execute files in the repository.
              </p>
              <p>
                If you trust the owner of the directory you can add an exception
                for this directory in order to continue.
              </p>
```

**File:** app/src/ui/add-repository/add-existing-repository.tsx (L69-77)
```typescript
  private onTrustDirectory = async () => {
    this.setState({ isTrustingRepository: true })
    const { repositoryUnsafePath, path } = this.state
    if (repositoryUnsafePath) {
      await addSafeDirectory(repositoryUnsafePath)
    }
    await this.validatePath(path)
    this.setState({ isTrustingRepository: false })
  }
```

**File:** app/src/lib/git/rev-parse.ts (L18-65)
```typescript
export async function getRepositoryType(path: string): Promise<RepositoryType> {
  if (!(await directoryExists(path))) {
    return { kind: 'missing' }
  }

  try {
    const result = await git(
      ['rev-parse', '--is-bare-repository', '--show-cdup', '--git-dir'],
      path,
      'getRepositoryType',
      { successExitCodes: new Set([0, 128]) }
    )

    if (result.exitCode === 0) {
      // Bare repositories will not include gitdir so we handle that separately
      if (result.stdout.startsWith('true\n')) {
        return { kind: 'bare' }
      }

      // --is-bare-repository and --show-cdup each produce a single line but
      // --git-dir could theoretically contain newlines so we parse the known
      // fields first and treat the remainder as the git dir. We use [\s\S]*
      // instead of .* for the git dir capture group because .* doesn't match
      // newlines whereas [\s\S]* matches any character including newlines.
      const match = result.stdout.match(/^(true|false)\n(.*)\n([\s\S]*)\n$/)

      if (match) {
        const [, isBare, cdup, gitDir] = match

        return isBare === 'true'
          ? { kind: 'bare' }
          : {
              kind: 'regular',
              topLevelWorkingDirectory: resolve(path, cdup),
              gitDir: resolve(path, gitDir),
            }
      }
    }

    const unsafeMatch =
      /fatal: detected dubious ownership in repository at '(.+)'/.exec(
        result.stderr
      )
    if (unsafeMatch) {
      return { kind: 'unsafe', path: unsafeMatch[1] }
    }

    return { kind: 'missing' }
```

**File:** app/src/lib/stores/app-store.ts (L8183-8207)
```typescript
    const rt = await getRepositoryType(path)

    if (rt.kind === 'regular') {
      // The repository has moved, so any main worktree we recorded before now
      // points at where it used to be. Resolve it again from the new location.
      await this.repositoriesStore.updateRepositoryPath(
        repository,
        rt.topLevelWorkingDirectory,
        rt.gitDir,
        await this.findMainWorktreePath(rt.topLevelWorkingDirectory)
      )
    } else if (rt.kind === 'unsafe') {
      // Git refuses to run in a repository it considers unsafe, so there's no
      // resolving the main worktree here. Drop the recorded path rather than
      // keep one we know is stale.
      await this.repositoriesStore.updateRepositoryPath(
        repository,
        path,
        undefined,
        undefined,
        true
      )
    } else {
      this.emitError(new Error(this.getInvalidRepoPathsMessage([path])))
    }
```
