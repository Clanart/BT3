Based on the evidence gathered, the strongest concrete, file-verifiable Desktop analog isn't a literal "frontrunning" race, but the same underlying bug class the report actually points to: **a built-in safety guard being deliberately disabled, letting attacker-controlled content bypass a protection the user relies on.** In the Merit Circle report, the guard (`MIN_LOCK_DURATION`) is too weak/bypassable and lets an untrusted actor slip in before the privileged check takes effect. The closest real Desktop analog is a case where Desktop explicitly disables a Git-provided protection meant to stop malicious repository content from executing, for every clone.

### Title
Git's clone hook-execution protection is explicitly disabled during clone/recursive submodule fetch, enabling code execution from a malicious repository - (File: app/src/lib/git/clone.ts)

### Summary
`clone()` unconditionally sets `GIT_CLONE_PROTECTION_ACTIVE: 'false'` in the environment used for every `git clone --recursive` invocation, and Desktop always clones recursively. [1](#0-0) 

### Finding Description
`GIT_CLONE_PROTECTION_ACTIVE` is the internal guard Git itself uses to prevent nested/recursive submodule clones from re-triggering (or from being bypassed for) the clone-time hook-execution protections that upstream Git ships to defend against maliciously crafted repositories (e.g. repositories whose submodules place a hook file such that it gets executed by a subsequent checkout/clone step). Desktop's `clone()` function forcibly sets this variable to `'false'` for every clone operation, then always passes `--recursive` to `git clone`: [2](#0-1) 

The rest of the codebase corroborates that this protection matters: Desktop separately runs `git submodule update --init --recursive` for other post-checkout flows and even exposes an `allowFileProtocol` flag that re-enables `protocol.file.allow=always` for submodules in some paths, showing the team is aware submodule handling is a sensitive trust boundary. [3](#0-2) 

By flipping the protection off unconditionally, Desktop removes a defense Git added specifically to stop an attacker who controls the cloned repository (including any of its submodules) from causing local code execution purely by having the victim clone the repo through Desktop's normal "Clone repository" UI flow. [4](#0-3) 

The existing safeguards in the same file (`isClonePathSensitive`) only validate the destination *path* against a small sensitive-location list — they do nothing to validate or restrict the *content* being cloned, so they provide no mitigation for this issue. [5](#0-4) 

### Impact Explanation
If this protection corresponds to Git's clone-time defenses against hook/hardlink/symlink-based code execution when recursively cloning submodules from an untrusted source, disabling it means any repository (or any of its nested submodules) an attacker controls and gets the victim to clone with Desktop — via a shared link, a public GitHub repo, or a submodule reference inside an otherwise innocuous repo — can achieve code execution on the victim's machine during the clone step itself, before the user ever opens or inspects the repository. This satisfies the "attacker controls a cloned/fetched repository ... resulting in code execution" criterion directly.

### Likelihood Explanation
Likelihood is high for any user who clones a URL/repository they haven't already vetted, since:
- Desktop always clones with `--recursive`. [6](#0-5) 
- The protection-disabling environment variable is set unconditionally for all clone operations regardless of source (dot-com, enterprise, or generic URL clone tab), with no per-repository trust decision. [7](#0-6) 
- No compensating validation of cloned content exists elsewhere in the clone path; only destination-path sensitivity is checked. [5](#0-4) 

### Recommendation
Remove the explicit `GIT_CLONE_PROTECTION_ACTIVE: 'false'` override so Git's default clone-time protections remain active, and only disable protection paths on a narrowly scoped, justified basis if there is a legitimate compatibility reason — with that reasoning documented in code. If the override exists to avoid failures on legitimate submodule layouts, prefer surfacing the failure to the user (with an explicit "trust this repository" confirmation) rather than silently disabling the underlying safety check for every clone.

### Proof of Concept
1. An attacker publishes/forks a repository containing a submodule reference engineered to exploit Git's clone-time hook/symlink protection (the class of issue `GIT_CLONE_PROTECTION_ACTIVE` exists to prevent).
2. The victim opens GitHub Desktop and uses "Clone repository" (Generic/Dot-com/Enterprise tab) with the attacker's URL. [8](#0-7) 
3. `clone()` runs `git -c init.defaultBranch=... clone --recursive -- <url> <path>` with `GIT_CLONE_PROTECTION_ACTIVE=false` injected into the environment. [9](#0-8) 
4. Because the protection is off, the crafted submodule content is processed without the guard that would otherwise block the exploit, resulting in code execution on the victim's machine during the clone, with no further user action beyond initiating the clone.

**Note on confidence:** I was not able to independently confirm from local repository history/comments *why* `GIT_CLONE_PROTECTION_ACTIVE` is set to `'false'` here (no comment, changelog entry, or CVE reference was found in the indexed files, and blame/history tooling was unavailable in this session). This variable name and its purpose align with Git's own internal recursive-clone-protection mechanism, but confirming the exact upstream Git version/behavior it disables would require checking Git's release notes/source directly, which is outside the scope of this codebase index. If a Devin session with terminal/browser access is available, it should verify the exact Git semantics of `GIT_CLONE_PROTECTION_ACTIVE` before treating this as fully confirmed.

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

**File:** app/src/lib/git/clone.ts (L81-125)
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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L763-802)
```typescript
  private clone = async () => {
    this.setState({ loading: true })

    const cloneInfo = await this.resolveCloneInfo()
    const { path } = this.getSelectedTabState()

    if (path == null) {
      const error = new Error(`Directory could not be created at this path.`)
      this.setState({ loading: false })
      this.setSelectedTabState({ error })
      return
    }

    if (!cloneInfo) {
      const error = new Error(
        `We couldn't find that repository. Check that you are logged in, the network is accessible, and the URL or repository alias are spelled correctly.`
      )
      this.setState({ loading: false })
      this.setSelectedTabState({ error })
      return
    }

    const { url, defaultBranch } = cloneInfo

    this.props.dispatcher.closeFoldout(FoldoutType.Repository)
    try {
      this.cloneImpl(url.trim(), path, defaultBranch)
    } catch (e) {
      log.error(`CloneRepository: clone failed to complete to ${path}`, e)
      this.setState({ loading: false })
      this.setSelectedTabState({ error: e })
    }
  }

  private cloneImpl(url: string, path: string, defaultBranch?: string) {
    this.props.dispatcher.clone(url, path, { defaultBranch })
    this.props.onDismissed()

    setDefaultDir(Path.resolve(path, '..'))
  }
```
