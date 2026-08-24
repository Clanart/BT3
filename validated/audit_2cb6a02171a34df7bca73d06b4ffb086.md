## Title
Deep-link `openRepo` URL is passed unvalidated into the Clone flow, allowing dangerous git transport schemes (e.g. `ext::`) to reach `git clone` - (File: `app/src/ui/dispatcher/dispatcher.ts`)

### Summary
`parseAppURL` only validates the `pr`, `branch`, and `filepath` query parameters of an `x-github-client://openRepo/...` deep link; it does not validate or restrict the scheme/content of the `url` path segment at all before returning an `open-repository-from-url` action. [1](#0-0) 

### Finding Description
`parseAppURL` extracts the deep-link path segment verbatim (`parsedPath = pathName.substring(1)`) and returns it as `url` for the `open-repository-from-url` action with no scheme allow-listing (only `branch`/`pr`/`filepath` get format checks). [2](#0-1) 

That `url` flows to `Dispatcher.openOrCloneRepository`, which — for a URL not matching any existing repository — opens the `CloneRepository` popup with `initialURL: url`, again with no scheme validation: [3](#0-2) 

Inside `CloneRepository`, the `initialURL` is fed straight into `updateUrl`, which only calls `parseRepositoryIdentifier` (used for deriving a clone directory name) — it performs no scheme/protocol check: [4](#0-3) [5](#0-4) 

When the user (or the automatically-invoked `Clone` on top-most dialog, if any auto-clone flow exists) submits, `clone()` calls `resolveCloneInfo()` (which just falls back to `{ url }` for non-recognized identifiers) and then hands the raw string straight to `dispatcher.clone(url, path, ...)`: [6](#0-5) 

At no point in this path — `parseAppURL` → `openOrCloneRepository` → `CloneRepository.updateUrl`/`clone`/`cloneImpl` → `dispatcher.clone` — is the URL scheme checked against an allow-list (e.g., restricting to `https:`, `http:`, `ssh:`, `git:`). The only checks that exist (`parseRepositoryIdentifier`, `sanitizeCloneName`, and the empty-folder path validation) are unrelated to transport safety; they concern clone directory naming/path emptiness, not the URL's transport scheme. This confirms the premise in the question: the flow does not reuse `parseAppURL`'s (nonexistent) URL validation, nor does the `CloneRepository` UI add its own.

### Impact Explanation
If the underlying git binary used by Desktop (via dugite) honors "user-invoked" protocol helpers like `ext::` or `file://` by default (which stock git does for directly-invoked, non-recursive `git clone` commands, as opposed to submodule-triggered clones), then a crafted `x-github-client://openRepo/ext::sh -c calc` (or similarly a `file://` path-based clone) reaching `dispatcher.clone()` could result in arbitrary command execution or unintended local file access, entirely without the user typing anything themselves beyond clicking a link and confirming the pre-filled Clone dialog. This matches the "code execution via a clicked deep link" impact category.

### Likelihood Explanation
The likelihood hinges on two unverified points in this codebase that I could not confirm with the tools available:
1. Whether `dispatcher.clone` / the underlying git-clone implementation (dugite) sets any `protocol.allow` / `GIT_ALLOW_PROTOCOL` restriction before invoking `git clone`. I did not locate such a restriction in the files reviewed, but I have not traced the full `Dispatcher.clone` → `git clone` implementation to be certain.
2. Whether the user still must click "Clone" in the pre-filled dialog (reducing "silent" execution) — this is a one-click, low-friction confirmation rather than a manual URL type-in, and the field is pre-populated with attacker content, so a user who trusts the app and clicks through is exposed.

Given the confirmed absence of any application-level scheme validation, and that git's own protocol restrictions for "user" protocols (`ext`, `file`) are permissive by default for top-level (non-recursive) clones, this is plausibly exploitable, but exact severity depends on the git/dugite version and default config bundled with Desktop, which was not verified.

### Recommendation
Add explicit scheme allow-listing (e.g., restrict to `https:`, `http:`, `ssh:`, `git:`) at the earliest point — ideally in `parseAppURL` for the `open-repository-from-url` action, and defensively again in `CloneRepository`'s `updateUrl`/`clone` before calling `dispatcher.clone` — rejecting/flagging any URL with `ext::`, `file://`, or other non-allow-listed transport schemes as an error rather than passing it through to `git clone`.

### Proof of Concept
1. Craft a deep link: `x-github-client://openRepo/ext::sh%20-c%20calc`.
2. `parseAppURL` returns `{ name: 'open-repository-from-url', url: 'ext::sh -c calc', branch: null, pr: null, filepath: null }` since only `branch`/`pr` are validated. [7](#0-6) 
3. The handler calls `openOrCloneRepository('ext::sh -c calc')`, which opens `CloneRepository` with `initialURL: 'ext::sh -c calc'`. [3](#0-2) 
4. `CloneRepository` pre-fills the URL field with this value via `updateUrl` with no rejection. [4](#0-3) 
5. Clicking "Clone" invokes `clone()` → `cloneImpl()` → `dispatcher.clone('ext::sh -c calc', path, ...)` with no scheme check performed anywhere in this component. [6](#0-5) 

Whether step 5 actually executes `sh -c calc` depends on git/dugite's own protocol allow-list behavior at the point `dispatcher.clone` invokes the native git clone — this final link was not verified in this review and should be confirmed by a background agent with terminal access to trace `Dispatcher.clone` into the dugite/git-clone implementation.

### Citations

**File:** app/src/lib/parse-app-url.ts (L66-125)
```typescript
export function parseAppURL(url: string): URLActionType {
  const parsedURL = URL.parse(url, true)
  const hostname = parsedURL.hostname
  const unknown: IUnknownAction = { name: 'unknown', url }
  if (!hostname) {
    return unknown
  }

  const query = parsedURL.query

  const actionName = hostname.toLowerCase()
  if (actionName === 'oauth') {
    const code = getQueryStringValue(query, 'code')
    const state = getQueryStringValue(query, 'state')
    if (code != null && state != null) {
      return { name: 'oauth', code, state }
    } else {
      return unknown
    }
  }

  // we require something resembling a URL first
  // - bail out if it's not defined
  // - bail out if you only have `/`
  const pathName = parsedURL.pathname
  if (!pathName || pathName.length <= 1) {
    return unknown
  }

  // Trim the trailing / from the URL
  const parsedPath = pathName.substring(1)

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

**File:** app/src/ui/clone-repository/clone-repository.tsx (L229-236)
```typescript
  public componentDidMount() {
    const initialURL = this.props.initialURL
    if (initialURL) {
      this.updateUrl(initialURL)
    }

    this.checkIsTopMostDialog(this.props.isTopMost)
  }
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L648-684)
```typescript
  private updateUrl = async (url: string) => {
    const parsed = parseRepositoryIdentifier(url)
    const tabState = this.getSelectedTabState()
    const lastParsedIdentifier = tabState.lastParsedIdentifier

    // If there is no path yet, just update the url
    if (tabState.path === null) {
      this.setSelectedTabState({ url }, this.validatePath)
      return
    }

    const safeName = parsed ? sanitizeCloneName(parsed.name) : null

    let newPath: string

    const dirPath = tabState.path
    if (lastParsedIdentifier) {
      if (safeName) {
        newPath = Path.join(Path.dirname(dirPath), safeName)
      } else {
        newPath = Path.dirname(dirPath)
      }
    } else if (safeName) {
      newPath = Path.join(dirPath, safeName)
    } else {
      newPath = dirPath
    }

    this.setSelectedTabState(
      {
        url,
        lastParsedIdentifier: parsed,
        path: newPath,
      },
      this.validatePath
    )
  }
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
