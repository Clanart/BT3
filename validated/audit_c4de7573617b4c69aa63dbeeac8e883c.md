### Title
Unvalidated GitHub API `clone_url` silently rewrites the local git remote, allowing hostname-changing remote hijack - ([File: app/src/lib/stores/updates/update-remote-url.ts](https://github.com/Kirstentat/desktop--016/blob/main/app/src/lib/stores/updates/update-remote-url.ts))

### Summary
`updateRemoteUrl()` is invoked automatically during background repository refresh to keep the local git `origin` remote in sync with GitHub API metadata. It rewrites the remote URL whenever the protocol matches and the previously-stored clone URL matched the current remote, but it **never verifies that the new `clone_url`'s hostname matches the old one**. An attacker able to influence the GitHub API response for the account's endpoint (e.g. a compromised/malicious GitHub Enterprise server, or a MITM proxy on a host the user has "Always trusted" via the untrusted-certificate flow) can return a `clone_url` pointing at a different host while keeping the same protocol, causing Desktop to silently repoint the user's git remote — corrupting where the next `push`/`fetch` goes, without any user confirmation.

### Finding Description
`repositoryWithRefreshedGitHubRepository()` fetches fresh repo metadata from the API and calls `updateRemoteUrl(gitStore, repository.gitHubRepository, apiRepo)`: [1](#0-0) 

`updateRemoteUrl()` decides whether to rewrite the remote based on three checks — `urlsMatch`, `protocolsMatch`, and `remoteUrlUnchanged` — but the only cross-check between the *old* and *new* URL is protocol equality, not hostname: [2](#0-1) 

`urlMatchesRemote()` (used to compute `urlsMatch`/`remoteUrlUnchanged`) does enforce hostname/owner/name equality, but it is only used to test *whether the URLs already match*, not to validate the *new* URL against the *original trusted host*: [3](#0-2) 

So the actual invariant that should hold — "Desktop must never silently change a repository's remote to point at a different host than the one the user configured/trusted" — is not enforced. `protocolsMatch` only rejects a scheme change (http↔ssh), and hostname changes pass through freely as long as the previous remote matched the previously stored `cloneURL` and the new URL differs.

The `apiRepo` object is fetched via `API.fromAccount(account)` using the account tied to the endpoint that was already matched to the repository, so the trust boundary is "whatever server answers requests to this account's endpoint." For github.com this is hard to abuse directly, but for GitHub Enterprise instances (self-hosted, admin-controlled, or reachable via a certificate the user chose to "Always trust" in `onContinueWithUntrustedCertificate`/`UntrustedCertificate`) an attacker controlling that endpoint's responses can return an arbitrary `clone_url` on a different host: [4](#0-3) [5](#0-4) 

Once the "Always trust" exception is set, all future API traffic to that host — including this background repository refresh — is trusted, giving the attacker a durable channel to serve a hostile API response.

### Impact Explanation
This causes silent corruption of what the user pushes and fetches: the local `origin` remote is rewritten in the background (as part of periodic repository indicator/status refresh, not a user-initiated action), without any dialog or confirmation. Subsequent `git push`/`git fetch`/`git pull` operations by the user will silently target the attacker-controlled remote, potentially exfiltrating private code/history to the attacker's server, or having the attacker serve malicious commits that get merged into the user's working copy on the next fetch. This matches the "silent corruption of what the user commits or pushes" and "attacker controls ... a GitHub API object ... or a git remote/proxy response" impact categories.

### Likelihood Explanation
Exploitation requires the attacker to control API responses for an endpoint the victim's Desktop instance already trusts — realistic for compromised/malicious GitHub Enterprise deployments, or for any endpoint where the user has previously clicked through the "Always trust" certificate flow (a documented, in-app-supported action, not "malware already on host" or physical access). No local access, no leaked credentials, and no unnatural extra user steps beyond normal use of Desktop against that endpoint are required — the rewrite happens automatically on background refresh.

### Recommendation
In `updateRemoteUrl()`, additionally require that the new `updatedRemoteUrl`'s hostname (as parsed by `parseRemote`) matches the hostname of the currently configured remote (or the account's endpoint hostname) before calling `gitStore.setRemoteURL(...)`. If the hostname differs, either refuse the automatic update or surface an explicit user-facing confirmation before changing the remote, mirroring the trust-sensitive UX already used for untrusted certificates and unsafe repository directories.

### Proof of Concept
1. Add a GitHub Enterprise account whose endpoint is `https://ghe.example.com`, and a repository whose `origin` remote is `https://ghe.example.com/acme/webapp.git`, matching the stored `gitHubRepository.cloneURL`.
2. Compromise (or MITM, after the user "Always trusts" its certificate) the `ghe.example.com` API endpoint so that `GET /repos/acme/webapp` returns `clone_url: "https://evil.example.com/acme/webapp.git"` (same `https` protocol, different host).
3. Wait for/trigger Desktop's periodic repository refresh, which calls `repositoryWithRefreshedGitHubRepository` → `updateRemoteUrl`.
4. Observe that `protocolsMatch` is true (both `https:`), `remoteUrlUnchanged` is true (old remote matched old stored `cloneURL`), and `urlsMatch` is false (new host differs) — all conditions in `updateRemoteUrl` are satisfied, so `gitStore.setRemoteURL('origin', 'https://evil.example.com/acme/webapp.git')` executes silently.
5. The user's next `git push`/`fetch` now targets `evil.example.com` with no warning shown.

### Citations

**File:** app/src/lib/stores/app-store.ts (L4887-4907)
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

**File:** app/src/lib/repository-matching.ts (L90-118)
```typescript
export function urlMatchesRemote(url: string | null, remote: IRemote): boolean {
  if (url == null) {
    return false
  }

  const cloneUrl = parseRemote(url)
  const remoteUrl = parseRemote(remote.url)

  if (remoteUrl == null || cloneUrl == null) {
    return false
  }

  if (!caseInsensitiveEquals(remoteUrl.hostname, cloneUrl.hostname)) {
    return false
  }

  if (remoteUrl.owner == null || cloneUrl.owner == null) {
    return false
  }

  if (remoteUrl.name == null || cloneUrl.name == null) {
    return false
  }

  return (
    caseInsensitiveEquals(remoteUrl.owner, cloneUrl.owner) &&
    caseInsensitiveEquals(remoteUrl.name, cloneUrl.name)
  )
}
```

**File:** app/src/ui/app.tsx (L1528-1535)
```typescript
  private onContinueWithUntrustedCertificate = (
    certificate: Electron.Certificate
  ) => {
    showCertificateTrustDialog(
      certificate,
      'Could not securely connect to the server, because its certificate is not trusted. Attackers might be trying to steal your information.\n\nTo connect unsafely, which may put your data at risk, you can “Always trust” the certificate and try again.'
    )
  }
```

**File:** app/src/ui/untrusted-certificate/untrusted-certificate.tsx (L36-52)
```typescript
    const host = URL.parse(this.props.url).hostname

    return (
      <Dialog
        title={__DARWIN__ ? 'Untrusted Server' : 'Untrusted server'}
        onDismissed={this.props.onDismissed}
        onSubmit={this.onContinue}
        type={__DARWIN__ ? 'warning' : 'error'}
      >
        <DialogContent>
          <p>
            GitHub Desktop cannot verify the identity of {host}. The certificate
            ({this.props.certificate.subjectName}) is invalid or untrusted.{' '}
            <strong>
              This may indicate attackers are trying to steal your data.
            </strong>
          </p>
```
