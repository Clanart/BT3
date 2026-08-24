## Title
Attacker-Controlled GitHub API `clone_url` Silently Rewrites Local Git Remote, Redirecting Future Fetch/Push - (File: `app/src/lib/stores/updates/update-remote-url.ts`)

## Summary
The Solana report's core issue is a *broken invariant during an authority transfer*: an unvalidated/attacker-influenced value ends up governing a subsequent privileged operation, with no safeguard that the new authority is actually trustworthy before it's accepted. The closest verifiable analog in GitHub Desktop is `updateRemoteUrl()`, which lets a value returned from the GitHub API (`apiRepo.clone_url`) silently overwrite the local git remote URL — the "authority" governing where the user's next fetch/push goes — with only a same-protocol check, no hostname pinning.

## Finding Description
`updateRemoteUrl` in [1](#0-0)  is invoked from `repositoryWithRefreshedGitHubRepository` in `app-store.ts` whenever Desktop refreshes GitHub repository metadata for a tracked repository: [2](#0-1) .

The logic:
```
const remoteUrl = gitStore.defaultRemote.url
const updatedRemoteUrl = apiRepo.clone_url
...
const protocolsMatch = parsedRemoteUrl.protocol === parsedUpdatedRemoteUrl.protocol
const remoteUrlUnchanged = urlMatchesRemote(gitHubRepository.cloneURL, gitStore.defaultRemote)

if (protocolsMatch && remoteUrlUnchanged && !urlsMatch) {
  await gitStore.setRemoteURL(gitStore.defaultRemote.name, updatedRemoteUrl)
}
```
`protocolsMatch` only checks that the URL scheme (e.g. `https:`) is the same — it does **not** pin the hostname/owner/name. `apiRepo.clone_url` is data fetched from the GitHub REST API (`api.fetchRepository`), an object whose fields are attacker-influenceable in scenarios such as: a GitHub Enterprise instance controlled or compromised by an attacker, a MITM/DNS-poisoned connection to the configured enterprise endpoint, or a repository whose `clone_url` metadata is otherwise not fully trustworthy at refresh time. Because the only gate is "does the scheme match," an API response with `clone_url: "https://evil.example/attacker/repo.git"` passes `protocolsMatch` and fails `urlsMatch` (since hostname differs), satisfying the condition to call `gitStore.setRemoteURL(...)`, which directly rewrites the on-disk git remote via `git remote set-url` (see `setRemoteURL` in `app/src/lib/git/remote.ts:57-64`).

This happens automatically as part of a background repository refresh — no user confirmation dialog, no diff shown, no warning that the remote URL changed. The existing "guard" (`remoteUrlUnchanged`) only prevents overwriting a remote the user manually customized; it does nothing to validate that the *new* URL is safe.

## Impact Explanation
Once the remote URL is silently rewritten, all subsequent `git fetch`/`git pull`/`git push` operations for that repository target the attacker-controlled host instead of the real GitHub repository. This directly matches the "silent corruption of what the user commits or pushes" impact class: a user pushing believing they're publishing to `github.com/owner/repo` is unknowingly sending their commits (including private code) to attacker infrastructure, and a subsequent fetch/pull could bring attacker-supplied objects into the user's working directory without any warning banner in the UI.

Credential exposure is bounded by `findGitHubTrampolineAccount`, which matches stored account credentials by comparing the *account's configured endpoint origin* to the request's origin [3](#0-2) , so a token for `github.com` would not automatically be handed to `evil.example`. However, if the attacker's redirected host mimics a known GHE endpoint's hostname (or if the compromised/malicious GHE server itself is the source of the malicious `clone_url` and shares that same origin), the credential helper's `isGitHubHost`/enterprise-detection heuristics in `trampoline-credential-helper.ts` (`getEndpointKind`) could still route stored credentials there, compounding the exposure beyond simple push/fetch redirection.

## Likelihood Explanation
This triggers during normal, unprivileged background operation — any time Desktop refreshes GitHub repository info for a repo the user already has open (e.g. on repository selection or periodic refresh), with no unusual user action required. The attacker precondition is control over the API response for a repository the user has associated with a GitHub/GHE account — realistic for a malicious or compromised GitHub Enterprise Server, or a network-position attacker against an HTTP(S) GHE endpoint. It does not require local/physical access, admin rights, leaked credentials, or social engineering, and it directly abuses the app's own auto-sync feature rather than any dependency CVE.

## Recommendation
In `updateRemoteUrl` (`app/src/lib/stores/updates/update-remote-url.ts`), require that the hostname of `updatedRemoteUrl` match the hostname already associated with the account/endpoint used to fetch the repository (or at minimum match the current remote's hostname) before calling `setRemoteURL`. If the hostname differs from what's expected, surface an explicit confirmation prompt to the user rather than silently rewriting the remote, mirroring how `AcceptBoss` should require validation before finalizing a sensitive authority change in the original report.

## Proof of Concept
1. Add a repository whose GitHub association points at a GitHub Enterprise endpoint under attacker influence (compromised GHES instance or MITM'd connection).
2. Have the attacker-controlled API respond to `GET /repos/{owner}/{name}` with `clone_url: "https://evil.example/attacker/repo.git"` (same `https:` scheme as the real remote).
3. Trigger Desktop's normal background refresh path that calls `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`.
4. Observe that `git remote -v` for the repository now shows `evil.example` as `origin`, with no dialog or warning shown to the user, per the logic in [4](#0-3) .
5. The user's next `git push` (via Desktop) silently sends commits to `evil.example`.

### Citations

**File:** app/src/lib/stores/updates/update-remote-url.ts (L7-45)
```typescript
export async function updateRemoteUrl(
  gitStore: GitStore,
  gitHubRepository: GitHubRepository,
  apiRepo: IAPIRepository
): Promise<void> {
  // I'm not sure when these early exit conditions would be met. But when they are
  // we don't have enough information to continue so exit early!
  if (gitStore.defaultRemote === null) {
    return
  }

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
}
```

**File:** app/src/lib/stores/app-store.ts (L4886-4907)
```typescript

    const { account, owner, name } = match
    const { endpoint } = account
    const api = API.fromAccount(account)
    const apiRepo = await api.fetchRepository(owner, name)

    if (apiRepo === null) {
      // If the request fails, we want to preserve the existing GitHub
      // repository info. But if we didn't have a GitHub repository already or
      // the endpoint changed, the skeleton repository is better than nothing.
      if (endpoint !== repository.gitHubRepository?.endpoint) {
        const ghRepo = await repoStore.upsertGitHubRepositoryFromMatch(match)
        return repoStore.setGitHubRepository(repository, ghRepo)
      }

      return repository
    }

    if (repository.gitHubRepository) {
      const gitStore = this.gitStoreCache.get(repository)
      await updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)
    }
```

**File:** app/src/lib/trampoline/find-account.ts (L20-29)
```typescript
export async function findGitHubTrampolineAccount(
  accountsStore: AccountsStore,
  remoteUrl: string
): Promise<Account | undefined> {
  const accounts = await accountsStore.getAll()
  const parsedUrl = new URL(remoteUrl)
  return accounts.find(
    a => new URL(getHTMLURL(a.endpoint)).origin === parsedUrl.origin
  )
}
```
