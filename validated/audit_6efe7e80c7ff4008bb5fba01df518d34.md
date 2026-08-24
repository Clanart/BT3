## Title
Git clone protection against malicious submodule/hook execution is explicitly disabled by GitHub Desktop - (File: `app/src/lib/git/clone.ts`)

### Summary
`clone()` in `app/src/lib/git/clone.ts` explicitly sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment before invoking `git clone --recursive` on an attacker-controlled URL. `GIT_CLONE_PROTECTION_ACTIVE` is the environment variable Git itself introduced to allow this exact safety check to be force-enabled/disabled around the `clone.protectNTFS` / hidden-`.git`-via-submodule-symlink class of clone-time attacks. GitHub Desktop is not merely leaving this at its default — it is actively forcing it to `'false'`, which is the disabling value, on every clone performed by the app. [1](#0-0) 

### Finding Description
When a user clones a repository through Desktop's "Clone repository" flow, `clone()` builds the Git invocation environment via `envForRemoteOperation(url)` and then unconditionally overrides it with `GIT_CLONE_PROTECTION_ACTIVE: 'false'`: [2](#0-1) 

The command executed is `git -c init.defaultBranch=<branch> clone --recursive -- <url> <path>` [3](#0-2)  — note `--recursive`, meaning any submodules referenced by the attacker's repository are automatically cloned and checked out as part of this single operation, with the clone-time protections that Git ships specifically to guard this path disabled.

Separately, the function does contain an application-level guard, `isClonePathSensitive()`, which blocks cloning directly into a small hardcoded list of sensitive directories (home dir, `.ssh`, `.gnupg`, `.config`, `AppData`, etc.) [4](#0-3) . This guard only validates the top-level destination path chosen by the user/UI — it does nothing to constrain what an attacker-controlled repository can place inside that destination once cloning (and recursive submodule cloning) begins, and it does not compensate for disabling Git's own clone-time protection.

`updateSubmodulesAfterOperation()`, used elsewhere (checkout, pull) to update submodules, has an explicit `allowFileProtocol` parameter that is guarded and defaults to `false` [5](#0-4) , showing the team is otherwise conscious of submodule-related protocol/security risk in other code paths — the disabling of `GIT_CLONE_PROTECTION_ACTIVE` in `clone.ts` stands out as inconsistent with that posture.

### Impact Explanation
An attacker who controls the content of a repository (its refs, tree, and `.gitmodules`) that a victim clones through Desktop's clone UI can potentially leverage the disabled clone-time protection together with `--recursive` submodule cloning to write or overwrite files outside the intended working tree, or to trigger execution via a submodule whose repository layout would normally be rejected/mitigated by Git's clone-time checks. This falls squarely under "attacker controls a cloned/fetched repository" resulting in "file write... outside the repo" or "code execution," which is explicitly in scope per the Valid Impact criteria.

### Likelihood Explanation
Cloning an arbitrary, untrusted URL (via "Clone repository" → URL tab, or via a `x-github-client://` / `github-windows://` clone deep link) is one of the most common unprivileged entry points in Desktop, requiring no special local access, admin rights, or pre-existing malware — only that the victim clones the attacker's repository, which is a completely natural GitHub Desktop workflow. Since `--recursive` is always passed, submodule resolution happens automatically without any extra user action.

### Recommendation
Do not force `GIT_CLONE_PROTECTION_ACTIVE` to `'false'`. Either omit the variable so Git's own default/safe behavior is used, or explicitly set it to `'true'` to keep the protection active during `clone --recursive` of untrusted, attacker-supplied repositories. If the override was added to work around a compatibility issue with a specific Git/dugite version, that workaround should be scoped narrowly (e.g., only for known-trusted internal operations) rather than applied unconditionally to every user-initiated clone of an arbitrary remote URL.

### Proof of Concept
1. Attacker publishes a Git repository containing a submodule entry in `.gitmodules` crafted to exploit the class of clone-time hook/symlink issue that `GIT_CLONE_PROTECTION_ACTIVE` is designed to prevent (e.g., a submodule path that collides with or escapes the intended working directory boundary on the victim's filesystem/OS configuration).
2. Attacker sends the victim a link to clone this repository through GitHub Desktop (via the Clone dialog or a `x-github-client://openRepo` style clone deep link).
3. Victim clicks "Clone" in Desktop.
4. Desktop calls `clone()`, which executes `git clone --recursive -- <attacker-url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` forced in the environment [2](#0-1) , `isClonePathSensitive()` only having validated the top-level destination and not the submodule’s internal layout [4](#0-3) .
5. With the protection disabled, the crafted submodule content is processed without the safety check Git added specifically for this scenario, resulting in unexpected file placement/write outside the intended repo boundary during the recursive submodule clone.

**Note on verification confidence:** I could not access Git's changelog/CVE details or Desktop's commit history/blame from the tools available in this session to confirm the exact CVE this environment variable corresponds to or find any accompanying justification/comment for setting it to `false` (no comment exists in the file next to that line). I recommend a Devin session with full repo/history access and network access to Git's official documentation to confirm the precise Git version behavior of `GIT_CLONE_PROTECTION_ACTIVE` and pinpoint when/why this override was introduced in Desktop before finalizing a fix.

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

**File:** app/src/lib/git/clone.ts (L68-125)
```typescript
export async function clone(
  url: string,
  path: string,
  options: CloneOptions,
  progressCallback?: (progress: ICloneProgress) => void
): Promise<void> {
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }

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
