### Title
Unvalidated URL scheme passed to `shell.openExternal` from attacker-controlled GitHub API `html_url` — ([File: app/src/main-process/main.ts])

### Summary
The `open-external` IPC handler in the main process forwards any string coming from the renderer directly to Electron's `shell.openExternal()` without validating or allowlisting the URL scheme. The renderer builds these URLs from `GitHubRepository.htmlURL`, a field populated verbatim from the GitHub API's `html_url` (and derived fields like `clone_url`/`ssh_url`), with no protocol/host validation anywhere in the chain. This mirrors the smart-contract bug class of "missing input validation" (no zero-address / no bound check) — here the missing check is "no scheme allowlist / no host validation" on a value that is used to trigger a privileged OS action.

### Finding Description
`ipcMain.handle('open-external', ...)` only inspects the string to decide whether to log it as "opening in browser"; it performs no validation before calling `shell.openExternal(path)`: [1](#0-0) 

This handler is reached via `openExternal` in `main-process-proxy`, wrapped by `app-shell.ts`'s `shell.openExternal`, and ultimately called by `AppStore._openInBrowser`: [2](#0-1) 

The URLs passed into `_openInBrowser` are frequently built from `GitHubRepository.htmlURL`, e.g. when viewing a repo on GitHub or opening a "Create Pull Request" page: [3](#0-2) [4](#0-3) 

`GitHubRepository.htmlURL` is a plain, unvalidated `string | null` set directly from the API response: [5](#0-4) 

`IAPIRepository.html_url` (and `clone_url`, `ssh_url`) are typed as raw strings straight from the JSON API response with no scheme/host validation performed anywhere before they're persisted or rendered into clickable links: [6](#0-5) 

By contrast, the enterprise-endpoint validator does perform this kind of check (protocol must be `https:`), proving the app already recognizes the need for such validation elsewhere but does not apply it to values coming out of the GitHub API/response objects themselves: [7](#0-6) 

Similarly, the BYOK URL validator for Copilot explicitly rejects `file://`, `javascript:`, `ftp://`, `data:` schemes — showing the pattern the app knows to apply but the `open-external` path skips entirely: [8](#0-7) 

The broken invariant: any value reaching `openExternal` should be constrained to `http:`/`https:` (and ideally to expected hosts), but the actual guard is "log if it looks like http/https, otherwise still call `shell.openExternal` unconditionally."

### Impact Explanation
`shell.openExternal` on Electron/Windows dispatches to the OS shell (`ShellExecute`), which can invoke arbitrary registered URL-scheme handlers. If a `html_url`/`clone_url` field returned by a malicious or compromised GitHub Enterprise server (or a MITM'd/proxy-tampered API response — both are attacker classes accepted by scope) contains something other than a normal `https://` GitHub URL — e.g. a `file://\\attacker-smb-share\payload` UNC path, a dangerous custom protocol registered by another installed application, or an OS-specific scheme known to have been abused for RCE via `shell.openExternal` in the Electron ecosystem — clicking "View on GitHub", "Create Pull Request", or similar actions in Desktop will invoke it without any user-visible warning about the destination being non-HTTP(S). This can lead to code execution or file access outside expectations, satisfying the "attacker controls a GitHub API object … and the result is code execution" impact class.

### Likelihood Explanation
Reaching this path requires a user to have added an account/endpoint whose API responses are attacker-influenced (malicious/compromised GitHub Enterprise Server) or a network position able to tamper with GitHub API responses to Desktop, then getting the user to click a normal-looking in-app action ("View on GitHub", "Create Pull Request", commit/PR links built from `htmlURL`). No unnatural steps are required beyond ordinary Desktop usage once such a repository is added — the click is a standard user action, not social engineering of the payload itself. This keeps it in-scope (GitHub API object / remote-response attacker) rather than requiring local access or leaked credentials.

### Recommendation
In the `open-external` IPC handler (`app/src/main-process/main.ts`), validate the scheme before calling `shell.openExternal`, allowlisting only `http:`/`https:` (matching the existing pattern in `enterprise-validate-url.ts` and the BYOK URL validator), and reject/prompt on anything else. Additionally, validate/normalize `html_url`, `clone_url`, and `ssh_url` when parsing `IAPIRepository` in `app/src/lib/api.ts` so malformed or non-HTTP(S) values can never be persisted into `GitHubRepository.htmlURL`/`cloneURL` in the first place.

### Proof of Concept
1. Add a GitHub Enterprise account pointed at a server under attacker control (or intercept/tamper with API traffic to a legitimate GHE instance).
2. Have the malicious server return a repository object whose `html_url` field is set to a non-HTTP(S) value, e.g. `file://\\attacker-share\payload.exe` or a locally-registered dangerous URI scheme, instead of a normal `https://` URL.
3. Desktop stores this value verbatim in `GitHubRepository.htmlURL` (`app/src/models/github-repository.ts:24`).
4. The user clicks "View on GitHub" (`app/src/ui/app.tsx:3377-3389`) or "Create Pull Request" (`app/src/lib/stores/app-store.ts:8535-8574`).
5. `AppStore._openInBrowser` calls `shell.openExternal(url)` (`app/src/lib/stores/app-store.ts:7595-7597`), which round-trips to the main process handler that performs no scheme validation before invoking Electron's `shell.openExternal` (`app/src/main-process/main.ts:581-597`).

Note: I could not fully verify how Electron's `shell.openExternal` behaves for every exotic scheme on the current Electron version bundled in this repo, nor find a specific OS-level RCE primitive already demonstrated in this codebase's test suite — that would require deeper testing against the actual Electron/OS combination, which is outside what static code search can confirm.

### Citations

**File:** app/src/main-process/main.ts (L581-597)
```typescript
  ipcMain.handle('open-external', async (_, path: string) => {
    const pathLowerCase = path.toLowerCase()
    if (
      pathLowerCase.startsWith('http://') ||
      pathLowerCase.startsWith('https://')
    ) {
      log.info(`opening in browser: ${path}`)
    }

    try {
      await shell.openExternal(path)
      return true
    } catch (e) {
      log.error(`Call to openExternal failed: '${e}'`)
      return false
    }
  })
```

**File:** app/src/lib/stores/app-store.ts (L7595-7597)
```typescript
  public _openInBrowser(url: string): Promise<boolean> {
    return shell.openExternal(url)
  }
```

**File:** app/src/lib/stores/app-store.ts (L8535-8574)
```typescript
  public async _openCreatePullRequestInBrowser(
    repository: Repository,
    compareBranch: Branch,
    baseBranch?: Branch
  ): Promise<void> {
    const gitHubRepository = repository.gitHubRepository
    if (!gitHubRepository) {
      return
    }

    const { parent, owner, name, htmlURL } = gitHubRepository
    const isForkContributingToParent =
      isForkedRepositoryContributingToParent(repository)

    const baseForkPreface =
      isForkContributingToParent && parent !== null
        ? `${parent.owner.login}:${parent.name}:`
        : ''
    const encodedBaseBranch =
      baseBranch !== undefined
        ? baseForkPreface +
          encodeURIComponent(baseBranch.nameWithoutRemote) +
          '...'
        : ''

    const compareForkPreface = isForkContributingToParent
      ? `${owner.login}:${name}:`
      : ''

    const encodedCompareBranch =
      compareForkPreface +
      encodeURIComponent(
        compareBranch.upstreamWithoutRemote ?? compareBranch.nameWithoutRemote
      )

    const compareString = `${encodedBaseBranch}${encodedCompareBranch}`
    const baseURL = `${htmlURL}/pull/new/${compareString}`

    await this._openInBrowser(baseURL)
  }
```

**File:** app/src/ui/app.tsx (L3377-3389)
```typescript
  private viewOnGitHub = (
    repository: Repository | CloningRepository | null
  ) => {
    if (!(repository instanceof Repository)) {
      return
    }

    const url = getGitHubHtmlUrl(repository)

    if (url) {
      this.props.dispatcher.openInBrowser(url)
    }
  }
```

**File:** app/src/models/github-repository.ts (L15-30)
```typescript
  public constructor(
    public readonly name: string,
    public readonly owner: Owner,
    /**
     * The ID of the repository in the app's local database. This is no relation
     * to the API ID.
     */
    public readonly dbID: number,
    public readonly isPrivate: boolean | null = null,
    public readonly htmlURL: string | null = null,
    public readonly cloneURL: string | null = null,
    public readonly issuesEnabled: boolean | null = null,
    public readonly isArchived: boolean | null = null,
    /** The user's permissions for this github repository. `null` if unknown. */
    public readonly permissions: GitHubRepositoryPermission = null,
    public readonly parent: GitHubRepository | null = null
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

**File:** app/src/ui/lib/enterprise-validate-url.ts (L14-45)
```typescript
export function validateURL(address: string): string {
  // ensure user has specified text and not just whitespace
  // we will interact with this server so we can be fairly
  // relaxed here about what we accept for the server name
  const trimmed = address.trim()
  if (trimmed.length === 0) {
    const error = new Error('Unknown address')
    error.name = InvalidURLErrorName
    throw error
  }

  let url = URL.parse(trimmed)
  if (!url.host) {
    // E.g., if they user entered 'ghe.io', let's assume they're using https.
    address = `https://${trimmed}`
    url = URL.parse(address)
  }

  if (!url.protocol) {
    const error = new Error('Invalid URL')
    error.name = InvalidURLErrorName
    throw error
  }

  if (url.protocol !== 'https:') {
    const error = new Error('Invalid protocol')
    error.name = InvalidProtocolErrorName
    throw error
  }

  return address
}
```

**File:** app/test/unit/copilot-byok-test.ts (L105-140)
```typescript
describe('isValidBYOKBaseUrl', () => {
  it('accepts https URLs', () => {
    assert.strictEqual(isValidBYOKBaseUrl('https://api.openai.com/v1'), true)
  })

  it('accepts http URLs that point at the local machine', () => {
    assert.strictEqual(isValidBYOKBaseUrl('http://localhost:11434/v1'), true)
    assert.strictEqual(isValidBYOKBaseUrl('http://127.0.0.1:11434/'), true)
    assert.strictEqual(isValidBYOKBaseUrl('http://[::1]:11434/'), true)
  })

  it('rejects http URLs that point at non-loopback hosts', () => {
    assert.strictEqual(isValidBYOKBaseUrl('http://api.openai.com/v1'), false)
    assert.strictEqual(isValidBYOKBaseUrl('http://192.168.1.5/'), false)
    assert.strictEqual(isValidBYOKBaseUrl('http://0.0.0.0:11434/'), false)
  })

  it('rejects file:// URLs', () => {
    assert.strictEqual(isValidBYOKBaseUrl('file:///etc/passwd'), false)
  })

  it('rejects javascript: URLs', () => {
    assert.strictEqual(isValidBYOKBaseUrl('javascript:alert(1)'), false)
  })

  it('rejects ftp:// and other non-http schemes', () => {
    assert.strictEqual(isValidBYOKBaseUrl('ftp://example.com/'), false)
    assert.strictEqual(isValidBYOKBaseUrl('data:text/plain,hi'), false)
  })

  it('rejects strings that are not absolute URLs', () => {
    assert.strictEqual(isValidBYOKBaseUrl(''), false)
    assert.strictEqual(isValidBYOKBaseUrl('not a url'), false)
    assert.strictEqual(isValidBYOKBaseUrl('/api/v1'), false)
  })
})
```
