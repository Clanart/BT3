### Title
Git `safe.directory` trust is granted per-path, not per-repository-identity, letting a re-used or shared path silently inherit prior trust - ([File: app/src/lib/git/config.ts])

### Summary
When GitHub Desktop encounters a repository whose on-disk ownership doesn't match the current user, it flags it as `unsafe` and lets the user click "Trust Repository". That action calls `addSafeDirectory`, which appends the raw filesystem path to the **global** `safe.directory` Git config key [1](#0-0) . The allowlist is keyed purely on path string, never on repository identity (remote URL, initial commit, or any content hash). Once a path is trusted, `getRepositoryType`/`git rev-parse` will always report it as `regular` for that path, regardless of what content or ownership subsequently occupies it [2](#0-1) , and Desktop's UI never re-prompts for that path again [3](#0-2) [4](#0-3) .

### Finding Description
This mirrors the `OnlyEOAPolicy` bug class: a security exception (an "allowlist" entry) that is supposed to apply to one specific, reviewed instance is instead stored in a scope shared by every future "consumer" that happens to match the same coarse key. In `OnlyEOAPolicy`, the key was "any consumer"; here the key is "any repository that lives at this filesystem path."

`addGlobalConfigValueIfMissing('safe.directory', path)` writes to the user's `~/.gitconfig` (`--global`), so the trust decision is not bound to the specific repository the user inspected when clicking "Trust Repository" — it is bound only to the absolute path string [5](#0-4) . If that same path is later repopulated with different content (e.g. a network share, a container bind-mount, a removable drive, or a directory reused by `_relocateRepository`/`_switchWorktree`), Desktop's ownership check will not fire again because `getRepositoryType` only inspects Git's own dubious-ownership detection, which is fully satisfied by the `safe.directory` entry [6](#0-5) [7](#0-6) . There is no secondary binding (e.g., to the `.git` directory's remote URL, or a content fingerprint) that would force Desktop to re-ask the user when the repository at that trusted path is effectively a different repository.

### Impact Explanation
Once a path is silently treated as trusted, Desktop will execute Git operations there without further confirmation. Git repository configuration can drive code execution through mechanisms such as `core.hooksPath`, smudge/clean filters, or credential helpers defined in that repository's local config — exactly the class of risk the "unsafe/dubious ownership" warning exists to gate (per Desktop's own warning text: "Adding untrusted repositories may automatically execute files in the repository" [8](#0-7) ). A path-scoped, content-blind allowlist means that guard can be bypassed for any repository that later occupies a previously-trusted path.

### Likelihood Explanation
This requires a path that was legitimately trusted once (e.g. a shared drive, mounted volume, or reused clone destination) to later be repopulated with different, attacker-influenced content — a scenario plausible in shared/multi-tenant environments (shared mounts, CI checkout directories, synced folders) but which does depend on some form of shared/reused storage location rather than a pure network-triggerable primitive. I was not able to find, within the indexed portion of the codebase, any additional binding (remote URL, commit hash, or repository UUID) that Desktop checks alongside the path before treating a directory as trusted, so I cannot rule out that this is the only guard in place — this is a genuine gap in what I could verify with available tools.

### Recommendation
Bind the "Trust Repository" exception to something more specific than a bare path — e.g., re-validate remote URL/identity at the time of the confirmation, or scope the granted trust to the specific worktree/`.git` directory the user reviewed rather than writing an unscoped, permanent `--global safe.directory` entry. At minimum, consider re-prompting when the underlying `.git` directory's identity (remote URL or initial commit) changes for a previously-trusted path.

### Proof of Concept
1. Point Desktop at a directory whose owner differs from the current user (e.g. a mounted network share) containing a legitimate repository; Desktop reports it as `unsafe` [9](#0-8) .
2. User clicks "Trust Repository" → `onTrustDirectory` → `addSafeDirectory(path)` writes `safe.directory=<path>` to the global `~/.gitconfig` [3](#0-2) [10](#0-9) .
3. At a later time, the content at that exact path is replaced (different remote, different owner/content) by whoever controls that shared location.
4. Desktop reopens the path; `getRepositoryType` returns `regular` (not `unsafe`) because the path is already present in `safe.directory`, so no trust prompt is shown and Desktop proceeds to run Git commands (and any repo-defined hooks/filters) against the new, untrusted content.

Given the dependency on a shared/reused storage path rather than a pure remote-triggerable vector, this analog is offered with the caveat above about unverified additional guards; if none exist, this constitutes a legitimate "unscoped-allowlist" analog to the reported `OnlyEOAPolicy` issue.

### Citations

**File:** app/src/lib/git/config.ts (L176-205)
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

/** Set the global config value by name. */
export async function addGlobalConfigValueIfMissing(
  name: string,
  value: string
): Promise<void> {
  const { stdout, exitCode } = await git(
    ['config', '--global', '-z', '--get-all', name, value],
    __dirname,
    'addGlobalConfigValue',
    { successExitCodes: new Set([0, 1]) }
  )

  if (exitCode === 1 || !stdout.split('\0').includes(value)) {
    await addGlobalConfigValue(name, value)
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

**File:** app/src/ui/missing-repository.tsx (L118-123)
```typescript
            <div className="details">
              <p>
                The Git repository at <Ref>{unsafePath}</Ref> appears to be
                owned by another user on your machine. Adding untrusted
                repositories may automatically execute files in the repository.
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

**File:** app/src/lib/stores/app-store.ts (L6069-6072)
```typescript
    // If the repository path isn't trusted we'll mark the repository as
    // missing. The missing repository view knows how to add a path to the
    // allow list.
    const missing = type.kind === 'unsafe'
```

**File:** app/src/lib/stores/app-store.ts (L8194-8204)
```typescript
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
```
