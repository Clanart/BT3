## Title
`openOrCloneRepository` passes attacker-controlled deep-link URL directly to `git clone` with no protocol allow-list, enabling remote-helper argument/command execution - ([File: app/src/lib/parse-app-url.ts])

### Summary
The report's underlying pattern is "spec says a class of inputs must be handled/validated across all variants, but the implementation only covers a subset, and there is no gate stopping the uncovered case from reaching a dangerous sink." In `ArmadaTreasuryGov`, ETH was an intended asset type but had no distribution path or validation. In GitHub Desktop, the `x-github-client://openRepo/...` deep-link handler (`parseAppURL`) validates *branch names* and *PR numbers* but performs **no protocol/scheme validation whatsoever on the repository URL itself** before it is threaded through to `git clone`. Only `https` is exercised by the "openRepo via HTTPS" tests and `git@host:...` by the SSH tests, but the parser and every downstream consumer accept **any string** as the `url` field.

### Finding Description
`parseAppURL` extracts everything after `openrepo/` in the deep-link path and returns it verbatim as `IOpenRepositoryFromURLAction.url`, with validation applied only to `branch` (via `testForInvalidChars`) and `pr` (numeric regex): [1](#0-0) 

No check exists on the `url` value — it is not restricted to `https://` or `git@host:` shapes, unlike the SSH/HTTPS regexes enforced elsewhere in `parseRemote` (`app/src/lib/remote-parsing.ts`), which only recognizes a fixed, safe set of `GitProtocol = 'ssh' | 'https'` forms: [2](#0-1) 

This deep-link action is dispatched to `Dispatcher.dispatchURLAction` → `openRepositoryFromUrl`, and the equivalent CLI path (`--cli-clone`) goes straight to `openOrCloneRepository(url)`: [3](#0-2) [4](#0-3) 

Ultimately the raw url string is appended as a positional argument to `git clone` in `app/src/lib/git/clone.ts`: [5](#0-4) 

Git's own `clone`/`fetch` machinery supports "remote helper" URL schemes such as `ext::`, `fd::`, or transport helpers resolved via `git-remote-<scheme>` on `PATH`. A URL like `ext::sh -c "calc.exe"` (or any arbitrary shell command) passed to `git clone` invokes the external command via Git's `ext` transport, executing attacker-supplied commands with the privileges of the Desktop process. Nothing in `parseAppURL`, `clone.ts`, or `envForRemoteOperation`/`envForProxy` inspects or restricts the URL scheme before it reaches `git clone`; the only protocol-aware logic (`envForProxy`'s `^(https?):\/\//` match) is used solely for proxy resolution and does not gate/deny non-http(s) schemes. [6](#0-5) 

This mirrors the report's spec-intent gap exactly: the spec (Desktop's own README/protocol documentation) implies that `openRepo`/`--cli-clone` handles legitimate GitHub `https`/`ssh` clone URLs, but the code has no enforcement mechanism preventing other Git URL schemes from reaching the same sink — the "missing distribute-for-ETH" analog is "missing scheme allow-list for clone."

### Impact Explanation
An attacker who gets a user to click a `x-github-client://openRepo/<malicious-url>` deep link (a normal, unprivileged web page or email link, no local access required) can achieve arbitrary command execution on the victim's machine the moment Desktop performs the clone, because `git clone` with an `ext::`-style URL executes a shell command as part of establishing the transport. This satisfies the "link/deep link the user clicks... resulting in code execution" impact category.

### Likelihood Explanation
Likelihood is high in relative terms: GitHub Desktop registers `x-github-client://` (and platform equivalents) as its default protocol handler at OS level (`app/src/main-process/main.ts`), so simply visiting a crafted webpage or link is enough to trigger the parse/clone path once the user confirms the "Clone" action in Desktop's UI — no other guard (URL allow-list, scheme check, confirmation of transport type) currently exists between the deep link and the `git clone` invocation.

### Recommendation
Add explicit scheme validation to `parseAppURL` (and to the `--cli-clone` CLI path) rejecting any URL that does not match the same safe `https://` or `git@host:owner/repo` shapes already enforced by `parseRemote` in `app/src/lib/remote-parsing.ts`, before constructing an `IOpenRepositoryFromURLAction` or invoking `clone()`. Reject scheme values other than `http:`, `https:`, and `ssh:`/SCP-style syntax, explicitly denying `ext::`, `fd::`, `file://`, and any other Git transport helper syntax.

### Proof of Concept
1. Register/trigger the `x-github-client://` protocol on a victim machine with Desktop installed (default after install).
2. Host a link: `x-github-client://openRepo/ext::sh -c "touch /tmp/pwned"` (URL-encoded as needed).
3. User clicks the link → OS routes it to Desktop → `handleAppURL` → `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'ext::sh -c "touch /tmp/pwned"', ... }` (no scheme check rejects it) → `dispatchURLAction` → `openRepositoryFromUrl` → eventually `clone(url, path, ...)` in `app/src/lib/git/clone.ts` appends the raw url to `git clone -- <url> <path>`.
4. Git's `ext` transport helper executes the embedded shell command, achieving code execution outside of any repository sandbox.

(Exact confirmation of the `openRepositoryFromUrl` implementation body in `app-store.ts`/`dispatcher.ts` beyond the citations shown was not retrievable within the tool budget; if it performs no additional scheme filtering — which the visible code paths and tests suggest — the PoC above holds. This should be verified against the live `openOrCloneRepository`/`openRepositoryFromUrl` implementation before filing.)

### Citations

**File:** app/src/lib/parse-app-url.ts (L98-125)
```typescript
  if (actionName === 'openrepo') {
    const pr = getQueryStringValue(query, 'pr')
    const branch = getQueryStringValue(query, 'branch')
    const filepath = getQueryStringValue(query, 'filepath')

    if (pr != null) {
      if (!/^\d+$/.test(pr)) {
        return unknown
      }

      // we also expect the branch for a forked PR to be a given ref format
      if (branch != null && !/^pr\/\d+$/.test(branch)) {
        return unknown
      }
    }

    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
  }
```

**File:** app/src/lib/remote-parsing.ts (L1-52)
```typescript
export type GitProtocol = 'ssh' | 'https'

interface IGitRemoteURL {
  readonly protocol: GitProtocol

  /** The hostname of the remote. */
  readonly hostname: string

  /**
   * The owner of the GitHub repository. This will be null if the URL doesn't
   * take the form of a GitHub repository URL (e.g., owner/name).
   */
  readonly owner: string

  /**
   * The name of the GitHub repository. This will be null if the URL doesn't
   * take the form of a GitHub repository URL (e.g., owner/name).
   */
  readonly name: string
}

// Examples:
// https://github.com/octocat/Hello-World.git
// https://github.com/octocat/Hello-World.git/
// git@github.com:octocat/Hello-World.git
// git:github.com/octocat/Hello-World.git
const remoteRegexes: ReadonlyArray<{ protocol: GitProtocol; regex: RegExp }> = [
  {
    protocol: 'https',
    regex: new RegExp(
      '^https?://(?:.+@)?(.+)/([^/]+)/([^/]+?)(?:/|\\.git/?)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git@(.+):([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp(
      '^(?:.+)@(.+\\.ghe\\.com):([^/]+)/([^/]+?)(?:/|\\.git)?$'
    ),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^git:(.+)/([^/]+)/([^/]+?)(?:/|\\.git)?$'),
  },
  {
    protocol: 'ssh',
    regex: new RegExp('^ssh://git@(.+)/(.+)/(.+?)(?:/|\\.git)?$'),
  },
]
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2051-2058)
```typescript
    if (action.kind === 'clone-url') {
      const { branch, url } = action

      if (branch) {
        await this.openBranchNameFromUrl(url, branch)
      } else {
        await this.openOrCloneRepository(url)
      }
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L2118-2120)
```typescript
      case 'open-repository-from-url':
        this.openRepositoryFromUrl(action)
        break
```

**File:** app/src/lib/git/clone.ts (L119-125)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
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
