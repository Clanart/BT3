### Title
Irrevocable `safe.directory` trust grants persist forever with no in-app revoke, allowing later re-use of a trusted path to run arbitrary Git hooks/config - (File: `app/src/lib/git/config.ts`)

### Summary
This maps the same "add-only, no-remove list" invariant from the backstop-pool report onto GitHub Desktop's directory-trust mechanism. When Desktop detects a repository with "dubious ownership" it offers a one-time **"Trust Repository"** action that permanently appends the path to Git's global `safe.directory` allow-list via `addSafeDirectory`/`addGlobalConfigValueIfMissing`. There is no UI, dispatcher action, or store method anywhere in the app to remove an entry from this list once added.

### Finding Description
`addSafeDirectory` and `addGlobalConfigValueIfMissing` only ever *add* a path to `safe.directory`: [1](#0-0) 

The only UI entry points that call this are the "Trust Repository" buttons in the add-existing-repository flow and the missing-repository recovery flow: [2](#0-1) [3](#0-2) 

Both simply call `addSafeDirectory(path)` and never expose any corresponding "untrust"/remove action. A `removeConfigValueInPath`/`--unset-all` helper exists in the same file, but it is never wired to `safe.directory` management anywhere in the codebase: [4](#0-3) 

`safe.directory` is Git's mitigation against "dubious ownership" — a directory that isn't owned by the current OS user, which Git otherwise refuses to treat as a trusted repo because its local `.git/config` (and hooks referenced from it, e.g. `core.fsmonitor`, `core.hooksPath`, `credential.helper`) could have been planted by another party: [5](#0-4) 

Once a user clicks "Trust Repository" for a given path, Desktop writes that literal path into the user's **global**, machine-wide gitconfig, permanently, with no path-scoping to a specific repository identity/commit/owner — only the raw filesystem path is recorded. If that same filesystem path is later reused for different content (a shared/synced folder, a Docker/Vagrant mount, a network share, `/tmp`-style scratch directories, a re-cloned or re-extracted archive, or any workflow that deletes and recreates a directory at the same path under a different actual owner/content), Git's dubious-ownership check is permanently bypassed for that path and Desktop will read and act on that repository's local config/hooks without ever prompting the user again.

### Impact Explanation
This breaks exactly the invariant the original report highlights: a security-relevant allow-list entry that can be added but never removed, so a decision made in good faith about one piece of content silently continues to apply to arbitrary future content at the same path. In this case, the consequence is not deposit/withdraw lockup, but unprompted execution of attacker-controlled repository configuration/hooks the next time Desktop performs Git operations against that path — a real code-execution primitive, not merely a UX inconvenience.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a victim to have trusted a path once (a normal, expected workflow step) and for that same path to later be repopulated with different, untrusted content — plausible on shared machines, CI/build agents, mounted/synced storage, or removable media, but not exploitable purely remotely without some path-reuse condition.

### Recommendation
Expose the `safe.directory` list in Desktop's settings/preferences UI and provide an explicit "untrust"/remove action that calls a `removeSafeDirectory` equivalent of `removeConfigValueInPath` (`git config --global --unset-all safe.directory <path>`, mirroring what `addSafeDirectory` does for adding). Consider also scoping trust decisions more strictly (e.g., re-validate ownership metadata, not just path string, before treating a previously trusted path as still safe) rather than relying solely on Git's global config accumulate-only behavior.

### Proof of Concept
1. On a shared/mounted environment, clone or open a directory `X` that another OS user owns; Desktop reports it as unsafe via `getRepositoryType` returning `{ kind: 'unsafe', path: X }`. [5](#0-4) 
2. User clicks "Trust Repository", triggering `onTrustDirectory` → `addSafeDirectory(X)`, permanently adding `X` to the global `safe.directory` list. [3](#0-2) 
3. Later, the underlying directory at path `X` is deleted and recreated by a different, malicious actor (e.g., another user on the shared box, a compromised CI job reusing the workspace path, or a remounted volume) containing a repository with a malicious `.git/config` (`core.fsmonitor`, `core.hooksPath`, hooks, etc.).
4. Desktop/Git will treat `X` as trusted (it is still listed in `safe.directory`) and execute the attacker's hooks/config on the next Git operation, with no re-prompt, because there is no way in the app to have revoked the earlier trust grant.

### Citations

**File:** app/src/lib/git/config.ts (L176-206)
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
}
```

**File:** app/src/lib/git/config.ts (L266-284)
```typescript
async function removeConfigValueInPath(
  name: string,
  path: string | null,
  env?: {
    HOME: string
  }
): Promise<void> {
  const options = env ? { env } : undefined

  const flags = ['config']

  if (!path) {
    flags.push('--global')
  }

  flags.push('--unset-all', name)

  await git(flags, path || __dirname, 'removeConfigValueInPath', options)
}
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
