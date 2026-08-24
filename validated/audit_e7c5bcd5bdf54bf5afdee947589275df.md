### Title
Startup race exposes GitHub Enterprise Authorization tokens to a window in which stale/incorrect origin scoping can occur - ([File: app/src/main-process/authenticated-image-filter.ts])

### Summary
The External Report's underlying bug class is a race condition during initialization: a subsystem begins operating (accepting/processing data) before its dependent state has been fully populated, causing decisions to be made against incomplete or default state. In GitHub Desktop, `installAuthenticatedImageFilter` installs a `webRequest.onBeforeSendHeaders` listener on `app.on('ready')`, immediately intercepting all outgoing requests, while the `originTokens` map it consults is only populated later, asynchronously, via the `update-accounts` IPC message sent from the renderer process. [1](#0-0) [2](#0-1) 

### Finding Description
`installAuthenticatedImageFilter` closes over a mutable `originTokens: Map<string, string>` that starts empty and is only replaced (not merged) when the renderer sends `update-accounts`, which itself depends on `AccountsStore` finishing its asynchronous `loadFromStore()` (which reads from disk and the OS keychain) and the `AppStore` constructor wiring `API.onTokenInvalidated`. [2](#0-1) [3](#0-2) 

The filter is fully "live" (attached to `onBeforeSendHeaders` and unconditionally evaluated for every request) from the moment `app.on('ready')` runs and `createWindow()` starts loading `index.html`, which is well before the renderer has parsed accounts from disk and posted them back to main via `update-accounts`. [4](#0-3) 

The security property this filter is meant to preserve is "only attach `Authorization: token …` when the origin has been validated as a signed-in account's endpoint." Because the map is entirely swapped (`originTokens = new Map(...)`) on each `update-accounts` call rather than incrementally reconciled against the previous state, any window in which the main process's view of accounts is stale relative to the renderer's (e.g., during multi-account sign-out/sign-in sequences, account removal, or GHE endpoint changes) can result in tokens being attached to origins that no longer represent a currently valid account-to-endpoint pairing, or a token being sent for the previous session's endpoint set until the next `update-accounts` round trip completes. Because Electron's `webContents` starts making requests (e.g., embedded avatar/asset images in already-rendered markdown/comments) as soon as HTML is parsed, and IPC delivery/ordering between renderer and main is not guaranteed to precede those network requests, there is a genuine "use before init" window analogous to the Prysm race: a piece of security-relevant state is consulted by an active handler before the initializer that's supposed to populate it correctly has run. [5](#0-4) 

Unlike `installSameOriginFilter`, which defensively strips `authorization`/`cookie` headers on cross-origin redirects regardless of initialization order, `installAuthenticatedImageFilter` has no analogous defense-in-depth for the case where its own state (`originTokens`) is incomplete or stale — it simply trusts whatever the last `update-accounts` message provided. [6](#0-5) 

### Impact Explanation
If an attacker can control content rendered inside the app (e.g., a malicious comment/PR body containing an `<img>` pointing at an attacker-controlled GHES-looking path, or a repo asset URL crafted to match `isGitHubRepoAssetPath`/`isEnterpriseAvatarPath`), and can win the narrow startup/account-transition race, the `Authorization: token <token>` header for a GitHub.com/GHE account could be attached to a request whose origin should not have received it at that point in time. This falls within valid impact criteria (credential/token exfiltration triggered by an attacker-controlled rendered object) if the origin-matching state is stale/incorrect during the race window.

### Likelihood Explanation
This requires: (1) the app being in the narrow window between process start and the first successful `update-accounts` round trip, or between an account removal and the corresponding `update-accounts` update, and (2) attacker-controlled content already being rendered that triggers a request matching `isEnterpriseAvatarPath`/`isGitHubRepoAssetPath` at exactly that moment. This is a real race but a narrow one, and I was not able to fully verify (within the scope of static code search) whether `update-accounts` is guaranteed to be sent and processed before any renderer-originated image request can fire, nor whether Electron enforces any ordering between IPC delivery and webRequest interception that would close this window. This uncertainty should be validated dynamically (e.g., with a Devin session that can run the app and trace the actual event ordering) before treating this as confirmed rather than a plausible analog.

### Recommendation
- Fail closed by default: don't attach the `Authorization` header for any origin until the main process has received at least one authoritative `update-accounts` message reflecting the current renderer state, and invalidate/clear `originTokens` synchronously (not just replace-on-next-update) whenever accounts change.
- Track a `generation`/`version` counter alongside `originTokens` and require that the `update-accounts` payload be a full authoritative snapshot tied to the same generation as the currently loaded `AccountsStore`, rejecting stale updates that arrived out of order.
- Add defense-in-depth similar to `installSameOriginFilter`: verify path/method value against the resolved origin's actual signed-in status per-request (not from a possibly-stale local cache) for high-sensitivity requests, or scope the Authorization header attachment to only same-session avatar/asset fetches triggered after `did-finish-load` and the renderer's initial `update-accounts` call.

### Proof of Concept
Not independently reproduced; this analysis is based on static code inspection of the initialization ordering in `app.on('ready')` versus `AccountsStore.loadFromStore()`/`update-accounts` IPC delivery. A concrete PoC would require instrumenting the Electron main process to (1) render an `<img>` tag matching `isGitHubRepoAssetPath` immediately at startup before `update-accounts` is delivered, and (2) sign out/switch accounts rapidly while a background image request is in flight, observing whether the wrong/stale token is attached via the `webRequest.onBeforeSendHeaders` handler in `installAuthenticatedImageFilter`. This would need to be done in a running instance of the app, which is outside the scope of static analysis — a Devin session with runtime access could confirm or refute the race window definitively. [2](#0-1) [1](#0-0)

### Citations

**File:** app/src/main-process/main.ts (L326-361)
```typescript
app.on('ready', () => {
  if (isDuplicateInstance || handlingSquirrelEvent) {
    return
  }

  readyTime = now() - launchTime

  possibleProtocols.forEach(protocol => setAsDefaultProtocolClient(protocol))

  createWindow()

  const orderedWebRequest = new OrderedWebRequest(
    session.defaultSession.webRequest
  )

  // Ensures auth-related headers won't traverse http redirects to hosts
  // on different origins than the originating request.
  installSameOriginFilter(orderedWebRequest)

  // Ensures Alive websocket sessions are initiated with an acceptable Origin
  installAliveOriginFilter(orderedWebRequest)

  // Adds an authorization header for requests of avatars on GHES and private
  // repo assets
  const updateAccounts = installAuthenticatedImageFilter(orderedWebRequest)

  Menu.setApplicationMenu(
    buildDefaultMenu({
      selectedShell: null,
      selectedExternalEditor: null,
      askForConfirmationOnRepositoryRemoval: false,
      askForConfirmationOnForcePush: false,
    })
  )

  ipcMain.on('update-accounts', (_, accounts) => updateAccounts(accounts))
```

**File:** app/src/main-process/authenticated-image-filter.ts (L26-63)
```typescript
export function installAuthenticatedImageFilter(
  orderedWebRequest: OrderedWebRequest
) {
  let originTokens = new Map<string, string>()

  orderedWebRequest.onBeforeSendHeaders.addEventListener(async details => {
    const { origin, pathname } = new URL(details.url)
    const token = originTokens.get(origin)

    if (
      token &&
      (isEnterpriseAvatarPath(pathname) || isGitHubRepoAssetPath(pathname))
    ) {
      return {
        requestHeaders: {
          ...details.requestHeaders,
          Authorization: `token ${token}`,
        },
      }
    }

    return {}
  })

  return (accounts: ReadonlyArray<EndpointToken>) => {
    originTokens = new Map(
      accounts.map(({ endpoint, token }) => [new URL(endpoint).origin, token])
    )

    // If we have a token for api.github.com, add another entry in our
    // tokens-by-origin map with the same token for github.com. This is
    // necessary for private image URLs.
    const dotComAPIEndpoint = getDotComAPIEndpoint()
    const dotComAPIToken = originTokens.get(dotComAPIEndpoint)
    if (dotComAPIToken) {
      originTokens.set(getHTMLURL(dotComAPIEndpoint), dotComAPIToken)
    }
  }
```

**File:** app/src/lib/stores/accounts-store.ts (L205-249)
```typescript
  /**
   * Load the users into memory from storage.
   */
  private async loadFromStore(): Promise<void> {
    const raw = this.dataStore.getItem('users')
    if (!raw || !raw.length) {
      return
    }

    const parsedAccounts: ReadonlyArray<IAccount> = JSON.parse(raw)
    const migratedAccounts = this.getMigratedGHEAccounts(parsedAccounts)
    const rawAccounts = migratedAccounts ?? parsedAccounts

    const accountsWithTokens = []
    for (const account of rawAccounts) {
      const accountWithoutToken = new Account(
        account.login,
        account.endpoint,
        '',
        account.emails,
        account.avatarURL,
        account.id,
        account.name,
        account.plan
      )

      const key = getKeyForAccount(accountWithoutToken)
      try {
        const token = await this.secureStore.getItem(key, account.login)
        accountsWithTokens.push(accountWithoutToken.withToken(token || ''))
      } catch (e) {
        log.error(`Error getting token for '${key}'. Skipping.`, e)

        this.emitError(e)
      }
    }

    this.accounts = sortAccounts(accountsWithTokens)
    // If any account was migrated, make sure to persist the new value
    if (migratedAccounts !== null) {
      this.save() // Save already emits an update
    } else {
      this.emitUpdate(this.accounts)
    }
  }
```

**File:** app/src/main-process/same-origin-filter.ts (L34-72)
```typescript
export function installSameOriginFilter(orderedWebRequest: OrderedWebRequest) {
  // A map between the request ID and the _initial_ request origin
  const requestOrigin = new Map<number, string>()
  const safeProtocols = new Set(['devtools:', 'file:', 'chrome-extension:'])
  const unsafeHeaders = new Set(['authentication', 'authorization', 'cookie'])

  orderedWebRequest.onBeforeRequest.addEventListener(async details => {
    const { protocol, origin } = new URL(details.url)

    // This is called once for the initial request and then once for each
    // "subrequest" thereafter, i.e. a request to https://foo/bar which gets
    // redirected to https://foo/baz will trigger this twice and we only
    // care about capturing the initial request origin
    if (!safeProtocols.has(protocol) && !requestOrigin.has(details.id)) {
      requestOrigin.set(details.id, origin)
    }

    return {}
  })

  orderedWebRequest.onBeforeSendHeaders.addEventListener(async details => {
    const initialOrigin = requestOrigin.get(details.id)
    const { origin } = new URL(details.url)

    if (initialOrigin === undefined || initialOrigin === origin) {
      return { requestHeaders: details.requestHeaders }
    }

    const sanitizedHeaders: Record<string, string> = {}

    for (const [k, v] of Object.entries(details.requestHeaders)) {
      if (!unsafeHeaders.has(k.toLowerCase())) {
        sanitizedHeaders[k] = v
      }
    }

    log.debug(`Sanitizing cross-origin redirect to ${origin}`)
    return { requestHeaders: sanitizedHeaders }
  })
```
