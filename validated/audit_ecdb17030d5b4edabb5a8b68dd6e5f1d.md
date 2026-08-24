### Title
Missing protocol validation on Pull Request head clone URL before `git remote add` / `fetch` allows arbitrary Git remote-helper invocation - ([File: app/src/lib/stores/app-store.ts])

### Summary
`_findPullRequestBranch` takes `headCloneUrl` straight from a GitHub API pull-request object and passes it unchecked into `addRemote()`, which shells out to `git remote add <name> <url>` and is later fetched. There is no invariant enforced that the URL must be a `https://` or `ssh://`/`git@` GitHub-style remote (the same shape `parseRemote`/`sanitizeCloneName` elsewhere in the codebase insist on for clone flows). This mirrors the report's bug class: a value derived from an external, only-partially-trusted source (there: oracle report numbers; here: a GitHub API `head.repo.clone_url` field) is consumed and persisted/executed without validating the invariants that the rest of the system assumes hold for remote URLs.

### Finding Description
`_findPullRequestBranch(repository, prNumber, headRepoOwner, headCloneUrl, headRefName)` in [1](#0-0)  does:

```
let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))
if (remote === undefined) {
  const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
  remote = await addRemote(repository, forkRemoteName, headCloneUrl)
}
```

`addRemote` simply does [2](#0-1) :

```
export async function addRemote(repository, name, url) {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')
  return { url, name }
}
```

No check is performed that `url` matches `parseRemote`'s allowed `https`/`ssh` shapes (as enforced for user-entered clone URLs in `app/src/lib/remote-parsing.ts` and `app/src/ui/lib/enterprise-validate-url.ts`, which explicitly reject non-`https:` protocols). Once added, the remote is fetched via `_fetchRemote` and the resulting ref checked out — meaning Git itself will interpret whatever scheme is embedded in `headCloneUrl` (e.g. `ext::`, `fd::`, or other Git "remote helper" schemes) when resolving/fetching the remote, and the environment used for the fetch (`envForRemoteOperation`) contains no protocol allow-list either — `envForProxy` only special-cases `http(s)` for proxy resolution and does not restrict which protocols Git is allowed to invoke [3](#0-2) .

This is exactly the "invariant not enforced before an oracle-sourced value is used/stored" pattern: elsewhere the codebase treats "is this a real https/ssh Git URL" as an invariant that must hold before use (`parseRemote`, `validateURL`, `sanitizeCloneName`), but the PR-checkout path skips that check entirely for `head.repo.clone_url`.

### Impact Explanation
If an attacker can influence the value that ends up in `pullRequest.head.repo.clone_url` / `headCloneUrl` (e.g. through a GitHub Enterprise Server the victim is configured against, a proxied/MITM'd API response, or any code path that constructs a `PullRequest`/`IAPIPullRequest` object from less-trusted data), Desktop will call `git remote add <fork-name> <attacker-url>` and then fetch it. Depending on the installed Git version and `protocol.*.allow` defaults, this can result in execution of an arbitrary external command via a Git remote helper (`ext::<command>`), i.e., code execution outside of the sandboxed repository context. This matches the "Valid Impact" category of "a git remote/proxy response ... result is code execution ... outside the repo."

### Likelihood Explanation
Medium: the primary trust boundary is the GitHub API response object itself. On github.com, `clone_url` is server-generated and not attacker-forgeable by a normal PR author, which limits likelihood for the hosted product. However, the code path itself provides zero defense-in-depth — there is no invariant check (unlike `parseRemote`/`sanitizeCloneName`/`validateURL` used elsewhere in Desktop) — so any future code path, GHE server compromise, or bug in API-object construction that supplies a malformed/malicious `clone_url` would be directly and silently exploitable with no additional guard to catch it.

### Recommendation
Before calling `addRemote`/`setRemoteURL` with a URL derived from a GitHub API object (`headCloneUrl`, `apiRepo.clone_url`, etc.), enforce the same invariant already used for user-entered clone URLs: parse the URL with `parseRemote` (or an equivalent GitHub-shape validator) and reject/refuse to add the remote if it does not resolve to a `https://` or `ssh://`/`git@` GitHub-style URL. This closes the gap between what the rest of the app assumes ("remote URLs are validated Git remote URLs") and what `_findPullRequestBranch` actually enforces (nothing).

### Proof of Concept
1. Construct (or intercept/modify) a pull-request API object such that `head.repo.clone_url` is set to `ext::sh -c "touch /tmp/pwned"` (or another Git remote-helper scheme).
2. Trigger the "Checkout this PR" flow in Desktop, or the `openrepo://...?pr=N` deep-link flow, so that `_checkoutPullRequest` → `_findPullRequestBranch(repository, prNumber, headRepoOwner, headCloneUrl, headRefName)` is invoked (`app/src/lib/stores/app-store.ts:8613-8721`).
3. Observe that `addRemote(repository, forkRemoteName, headCloneUrl)` runs `git remote add <name> "ext::sh -c ..."` with no validation, and the subsequent `_fetchRemote` fetch causes Git to invoke the embedded command.

Note: I was unable to fully verify, from the available index, whether `clone_url` for pull requests can actually be attacker-controlled end-to-end for github.com-hosted repositories (this typically requires a compromised/malicious GitHub Enterprise Server or a flaw in how the PR object is constructed elsewhere) — a full assessment of exploitability would need a Devin session with access to the complete API client/`IAPIPullRequest` construction code and to test actual Git remote-helper behavior on the bundled Git binary.

### Citations

**File:** app/src/lib/stores/app-store.ts (L8633-8660)
```typescript
  public async _findPullRequestBranch(
    repository: RepositoryWithGitHubRepository,
    prNumber: number,
    headRepoOwner: string,
    headCloneUrl: string,
    headRefName: string
  ): Promise<Branch | undefined> {
    const gitStore = this.gitStoreCache.get(repository)
    const remotes = await getRemotes(repository)

    // Find an existing remote (regardless if set up by us or outside of
    // Desktop).
    let remote = remotes.find(r => urlMatchesRemote(headCloneUrl, r))

    // If we can't find one we'll create a Desktop fork remote.
    if (remote === undefined) {
      try {
        const forkRemoteName = forkPullRequestRemoteName(headRepoOwner)
        remote = await addRemote(repository, forkRemoteName, headCloneUrl)
      } catch (e) {
        this.emitError(
          new Error(
            `Couldn't find PR branch, adding remote failed: ${e.message}`
          )
        )
        return
      }
    }
```

**File:** app/src/lib/git/remote.ts (L28-37)
```typescript
/** Add a new remote with the given URL. */
export async function addRemote(
  repository: Repository,
  name: string,
  url: string
): Promise<IRemote> {
  await git(['remote', 'add', name, url], repository.path, 'addRemote')

  return { url, name }
}
```

**File:** app/src/lib/git/environment.ts (L76-104)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}

/**
 * Not intended to be used directly. Exported only in order to
 * allow for testing.
 *
 * @param remoteUrl The remote url to resolve a proxy for.
 * @param env       The current environment variables, defaults
 *                  to `process.env`
 * @param resolve   The method to use when resolving the proxy url,
 *                  defaults to `resolveGitProxy`
 */
export async function envForProxy(
  remoteUrl: string,
  env: NodeJS.ProcessEnv = process.env,
  resolve: (url: string) => Promise<string | undefined> = resolveGitProxy
): Promise<Record<string, string | undefined> | undefined> {
  const protocolMatch = /^(https?):\/\//i.exec(remoteUrl)

  // We can only resolve and use a proxy for the protocols where cURL
  // would be involved (i.e http and https). git:// relies on ssh.
  if (protocolMatch === null) {
    return
  }
```
