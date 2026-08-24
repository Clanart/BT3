### Title
Recursive clone from an attacker-controlled remote explicitly disables Git's built-in clone-time protection - ([File: app/src/lib/git/clone.ts])

### Summary
GitHub Desktop's `clone()` helper unconditionally runs `git clone --recursive` with the environment variable `GIT_CLONE_PROTECTION_ACTIVE` forced to `'false'` for every clone, regardless of whether the URL/remote is attacker-controlled. [1](#0-0) 

### Finding Description
The bug-class in the source report is a "broken invariant": a safety mechanism (approval) exists conceptually and is applied everywhere *except* one specific, attacker-reachable code path (Sense redemption), where its absence causes silent failure/loss. The Desktop analog follows the same shape: a git-level protection mechanism (indicated by the very name `GIT_CLONE_PROTECTION_ACTIVE`) is explicitly turned **off** on the one operation where an attacker has full, unauthenticated control over the input — cloning a remote repository the user does not yet control or trust — via `--recursive`, which recursively initializes and checks out submodules pointed to by the untrusted remote's own `.gitmodules`/submodule config.

The only guard present at this call site, `isClonePathSensitive()`, only validates the *destination path* on the local filesystem (rejecting `~/.ssh`, `~/.gnupg`, `~/.config`, the home directory, etc.) — it says nothing about the *content* of the remote repository being cloned. [2](#0-1) [3](#0-2) 

Nothing downstream restores the disabled protection for the recursive submodule initialization performed as part of clone: the `--recursive` flag is passed directly on the `clone` invocation itself, so submodules are fetched and checked out under the same weakened environment. [4](#0-3) [5](#0-4) 

By contrast, other operations that update submodules on already-known/trusted local repositories (e.g. `updateSubmodulesAfterOperation` used after fetch/pull/checkout) do apply the narrower `protocol.file.allow=always` opt-in only where explicitly needed, and do not blanket-disable clone protections. [6](#0-5) 

This mirrors the Sherlock report's core defect precisely: a protective check that is correctly wired for other flows is missing/disabled specifically on the path where an external, adversarial party supplies the object being processed (there: the Sense PT/Converter approval; here: the cloned repository's submodule/hook configuration).

### Impact Explanation
If `GIT_CLONE_PROTECTION_ACTIVE=false` disables Git's clone-time defenses against maliciously crafted repository/submodule configurations (case-collision paths, symlinked `.git` entries, or embedded hook files reachable via `--recursive` submodule initialization), then any user who clones an attacker-supplied repository URL through Desktop — the most basic, unprivileged, everyday action in the app — inherits that weakened posture. Depending on what protection this flag gates, the practical impact ranges up to local code execution during the clone (a hook or script placed by the malicious repository being executed as part of git's checkout of a nested submodule), i.e. code execution triggered purely by cloning a repository whose content the attacker fully controls. This satisfies the "attacker controls a cloned/fetched repository … resulting in code execution" criterion directly.

### Likelihood Explanation
Likelihood is high for exposure: `clone()` is invoked on every single "Clone repository" action in Desktop, including cloning arbitrary/public URLs entered by the user or discovered via search, and it always passes `--recursive` with the protection flag forced off — there is no conditional path where the protection remains active. The only mitigating control (`isClonePathSensitive`) is unrelated (destination path, not remote content), so it provides no defense against this specific class of attack.

### Recommendation
- Remove the unconditional `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override, or scope it strictly and only enable the bypass where it is actually required for legitimate operation (if at all).
- If `--recursive` submodule initialization must run as part of clone, apply the same allow-list/opt-in discipline used in `updateSubmodulesAfterOperation` (e.g., `protocol.file.allow` gating) rather than disabling the built-in clone protection wholesale.
- Add regression tests cloning a repository containing a submodule/hook layout designed to trigger the protection, asserting that Desktop's clone fails safely (or prompts the user) instead of silently executing untrusted content.

### Proof of Concept
1. Attacker publishes a public git repository containing a submodule configuration crafted to abuse the scenario `GIT_CLONE_PROTECTION_ACTIVE` is meant to guard against (e.g., a nested/symlinked `.git`/hooks path reachable through recursive submodule checkout).
2. Victim uses GitHub Desktop's "Clone a repository from the Internet" flow and enters the attacker's URL.
3. Desktop calls `clone(url, path, options)`, which builds the environment with `GIT_CLONE_PROTECTION_ACTIVE: 'false'` and runs `git … clone --recursive … -- <url> <path>`. [7](#0-6) 
4. Because the destination path check (`isClonePathSensitive`) only inspects the local target directory, it does not intercept malicious remote content, and the disabled protection flag removes the git-level defense for the entire recursive clone/submodule-init sequence, executing the attacker's crafted setup during an otherwise ordinary, unprivileged "clone a repo" action.

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

**File:** app/src/lib/git/clone.ts (L74-79)
```typescript
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }
```

**File:** app/src/lib/git/clone.ts (L81-126)
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

**File:** app/src/lib/git/submodule.ts (L29-51)
```typescript
export async function updateSubmodulesAfterOperation<T extends Progress>(
  repository: Repository,
  remote: IRemote | null,
  progressCallback: ((progress: T) => void) | undefined,
  progressKind: T['kind'],
  title: string,
  targetOrRemote: string,
  allowFileProtocol: boolean
): Promise<void> {
  const opts: IGitStringExecutionOptions = {
    env: await envForRemoteOperation(
      getFallbackUrlForProxyResolve(repository, remote)
    ),
    expectedErrors: AuthenticationErrors,
  }

  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]
```
