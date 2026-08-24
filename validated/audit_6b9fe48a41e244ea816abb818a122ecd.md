### Title
Unvalidated fork `clone_url` reaches `git remote add`, enabling `ext::`/`fd::` remote-helper command execution - (File: `app/src/lib/git/remote.ts`)

### Summary
`GitStore.ensureUpstreamRemoteURL` passes an attacker-controlled URL (a PR's `head.repo.clone_url`, sourced from the GitHub API) straight into `addRemote`, which builds the git argv `['remote', 'add', name, url]` with no validation of the `url` value.

### Finding Description
`addRemote` in `app/src/lib/git/remote.ts` takes `url` and hands it unmodified to `git`: [1](#0-0) 

There is no check anywhere in this call chain (`git-store.ts` → `remote.ts` → `core.ts` → `dugite`'s `exec`) that rejects:
- URLs beginning with `-` (which could be parsed as an option to `git remote add`), or
- URLs using git's "smart" transport-helper schemes such as `ext::` or `fd::`, which cause git to spawn an arbitrary shell command when the remote is later fetched/pushed/pulled.

I confirmed via `grep_search` across the repo that there is no `GIT_ALLOW_PROTOCOL` environment variable set, no protocol allowlist, and no URL-scheme sanitization function anywhere in `app/src/lib/git/environment.ts`, `app/src/lib/git/core.ts`, or `app/src/lib/git/remote.ts`. `envForRemoteOperation`/`envForProxy` only look for `https?://` to decide whether to set an HTTP(S) proxy — they don't restrict or reject other schemes: [2](#0-1) 

`core.ts`'s `git()` wrapper (used by `addRemote`) also does no argument or URL sanitization; it simply forwards `args` to dugite's `exec`: [3](#0-2) 

Note an important nuance: `git remote add <name> <url>` by itself only writes the URL into `.git/config`; it does not invoke the transport helper. The transport helper (and thus the `ext::`/`fd::` command execution) would only run when git subsequently performs a network operation against that remote (fetch/pull/push/ls-remote), which is a normal part of the pull-request/upstream workflows this remote is added for (e.g., checking out or updating a PR branch triggers a fetch from the newly-added remote).

### Impact Explanation
If the smuggled URL scheme (`ext::` or `fd::`) reaches a subsequent git fetch/pull/push against the newly added remote, git will execute the attacker-supplied shell command with the privileges of the user running GitHub Desktop — i.e., arbitrary local code execution purely by having a malicious PR's fork metadata processed by Desktop.

### Likelihood Explanation
The `clone_url` field of a pull request's head repository is fully attacker-controlled (any GitHub user can fork a repo and rename/relocate nothing — however note: `clone_url` is normally derived by GitHub's API from the repository's actual identity/host, so an attacker would need the ability to make this field contain `ext::...`, which is not how GitHub's real API populates `clone_url` for a hosted fork). This is the key uncertainty: on real github.com, `clone_url` is server-generated (`https://github.com/<owner>/<repo>.git`) and not attacker-editable text, so this exploit path likely requires either (a) a custom/malicious GitHub Enterprise API endpoint the user has configured, or (b) some other means of getting an arbitrary string into that field. I was not able to fully verify within this pass whether Desktop validates or restricts the API/Enterprise endpoint origin before trusting `clone_url` values, which affects how "unprivileged" this attack really is.

### Recommendation
- Validate remote URLs before calling `addRemote`/`setRemoteURL`: reject values starting with `-`, and reject non-standard transport schemes (`ext::`, `fd::`, or anything not in an allowlist of `https:`, `http:`, `git:`, `ssh:`, or `scp`-like syntax).
- Alternatively/additionally, set `GIT_ALLOW_PROTOCOL` (or equivalent `protocol.*.allow` config) in the environment used for all git invocations to restrict transports to `http`, `https`, `ssh`, and `git`.
- Insert a literal `--` separator before the URL positional argument in `addRemote`/`setRemoteURL` argv construction to prevent option-injection via a leading `-`.

### Proof of Concept
Not independently verified end-to-end in this pass; based on the code paths reviewed, the theoretical PoC is: craft a PR object whose `head.repo.clone_url` is `ext::sh -c touch$IFS/tmp/pwned`, get `GitStore.ensureUpstreamRemoteURL` to call `addRemote`/`setRemoteURL` with that value, then trigger any fetch/push/pull against that remote (e.g., checking out the PR) — dugite/git would invoke the `ext::` transport helper. I was unable to confirm within this pass whether Desktop performs an automatic fetch against the newly-added upstream remote immediately after `ensureUpstreamRemoteURL`, or whether `clone_url` can, in practice, be attacker-manipulated for a hosted github.com repository versus only for a rogue/malicious GitHub Enterprise server the user has already added as an account. This should be validated with a live Devin session tracing the exact call sites of `ensureUpstreamRemoteURL` in `app/src/lib/stores/app-store.ts` and `app/src/lib/stores/git-store.ts`, and whether a fetch immediately follows.

### Citations

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

**File:** app/src/lib/git/environment.ts (L93-104)
```typescript
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

**File:** app/src/lib/git/core.ts (L225-237)
```typescript
export async function git(
  args: string[],
  path: string,
  name: string,
  options?: IGitExecutionOptions
): Promise<IGitResult> {
  const defaultOptions: IGitExecutionOptions = {
    successExitCodes: new Set([0]),
    expectedErrors: new Set(),
    maxBuffer: options?.encoding === 'buffer' ? Infinity : kStringMaxLength,
  }

  const opts = { ...defaultOptions, ...options }
```
