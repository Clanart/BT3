### Title
`getAliveWebSocketURL` trusts the server-supplied websocket URL without validating its host/scheme before connecting - ([File: app/src/lib/api.ts])

### Summary
This is the closest verified analog to the Chainlink issue's broken invariant: *"an external, potentially adversarial data source's response is consumed and acted upon directly, with no validation of its content before it drives a security-relevant decision."* In the Chainlink report, `latestRoundData()` output (price) is trusted and used unchecked. In GitHub Desktop, `API.getAliveWebSocketURL()` fetches a JSON payload from `/alive_internal/websocket-url` and returns `websocket.url` straight from the parsed response with no validation of scheme or hostname, before that string is handed to `AliveSession`, which opens a raw WebSocket connection to it. [1](#0-0) 

### Finding Description
`getAliveWebSocketURL` deserializes the HTTP response body via `parsedResponse<IAPIAliveWebSocket>(res)` and returns `websocket.url` unmodified: [1](#0-0) 

`parsedResponse`/`deserialize` in `app/src/lib/http.ts` explicitly documents that it "doesn't validate the expected shape, and will only fail if it encounters invalid JSON": [2](#0-1) 

That unchecked `url` is then passed directly into `new AliveSession(webSocketUrl, ...)` in `AliveStore.createSessionForAccount`, which is the object that actually performs the WebSocket connection: [3](#0-2) 

The only mitigating control I can find is `installAliveOriginFilter`, a main-process `webRequest` filter that rewrites the `Origin` header — but only for URLs whose `protocol` is `wss:` and whose `host` matches `alive.github.com` or `alive.*.ghe.com`; for any other host/protocol it is a no-op and simply lets the connection proceed unmodified: [4](#0-3) 

Critically, this filter only *adjusts the Origin header*; it does not cancel or block connections to URLs outside the expected host pattern. There is no code path that validates `websocket.url` itself belongs to the expected `alive.github.com`/`alive.*.ghe.com` domain before the connection is attempted — exactly the same class of gap as the Chainlink report: the response is consumed for a security-sensitive action (opening a network channel from within the authenticated session) without checking that its value is within expected bounds.

### Impact Explanation
The `/alive_internal/websocket-url` endpoint is queried from the account's configured GitHub.com/GHES `endpoint`, authenticated with the user's token (`this.ghRequest`, which sends `Authorization: Bearer <token>` per `app/src/lib/http.ts`). If a GitHub Enterprise Server instance is compromised or a network path to it is intercepted (a malicious/compromised GHES admin, a MITM on an internal/self-signed GHES the user has already trusted, or a rogue reverse proxy in front of it — all "attacker controls a git remote/proxy response" scenarios explicitly in scope), the attacker can return an arbitrary `url` value in the JSON body. Because that value is unvalidated, Desktop will initiate a WebSocket connection to an attacker-chosen host from inside the signed-in Desktop process. Depending on what the `AliveSession`/websocket client sends on connect (subscription topics, channel names obtained via `getAliveDesktopChannel`, potentially retry/reconnect metadata), this creates a channel for exfiltrating session-scoped data to an attacker-controlled endpoint, and turns an otherwise passive "notifications" feature into an SSRF-like egress primitive originating from the authenticated desktop client.

### Likelihood Explanation
Likelihood is bounded by the fact that dotcom's `alive.github.com` endpoint is presumably not attacker-controlled, and `supportsAliveSessions` gates this to accounts where the endpoint is either dotcom or (per `endpoint-capabilities.ts`) satisfies constraints; GHES support isn't enabled by `supportsAliveSessions` (`dotcom: true` only) as far as I can tell, which narrows the practically reachable attack surface today. However, the underlying code defect — accepting `websocket.url` from the API response with zero validation — remains present regardless of which endpoint is targeted, and would immediately become exploitable if Alive is ever enabled for GHES/GHE.com accounts, or if any single dotcom API response can be manipulated (e.g., a malicious reverse proxy sitting between Desktop and GitHub.com for enterprise-managed devices). I could not verify at what layer (if any) `AliveSession` from the `@github/alive-client` package performs its own scheme/host validation, since that package's source isn't part of this repository's index — this is a real gap in my analysis.

### Recommendation
Validate `websocket.url` in `getAliveWebSocketURL` before returning it: parse it and assert `protocol === 'wss:'` and that the hostname matches the same allow-list already encoded in `installAliveOriginFilter` (`^alive\.github\.com$` or `^alive\.(.*)\.ghe\.com$`), and reject/throw otherwise. Additionally, harden `installAliveOriginFilter` (or add a companion `onBeforeRequest` filter) to actively cancel WebSocket connection attempts to hosts outside the allow-list rather than merely skipping the `Origin`-header rewrite.

### Proof of Concept
1. Sign in to a GitHub Enterprise Server account in Desktop where the attacker controls (or can MITM) the GHES instance or a proxy in front of it.
2. Desktop calls `GET /alive_internal/websocket-url` as part of `AliveStore.createSessionForAccount` (`app/src/lib/stores/alive-store.ts:158-195`).
3. The attacker-controlled server/proxy responds with `{"url": "wss://attacker.example.com/ws"}` instead of the legitimate Alive endpoint.
4. `getAliveWebSocketURL` returns this value unchanged (`app/src/lib/api.ts:885-897`), since `parsedResponse`/`deserialize` never validates response shape or field contents (`app/src/lib/http.ts:57-76`).
5. `AliveStore` passes this URL straight into `new AliveSession(webSocketUrl, ...)`, and Desktop opens a WebSocket connection to `attacker.example.com` from the signed-in client. `installAliveOriginFilter` does not block this — it only rewrites the `Origin` header for hosts that already match the expected pattern, and is a no-op for `attacker.example.com` (`app/src/main-process/alive-origin-filter.ts:1-35`).

### Citations

**File:** app/src/lib/api.ts (L885-897)
```typescript
  public async getAliveWebSocketURL(): Promise<string | null> {
    try {
      const res = await this.ghRequest('GET', '/alive_internal/websocket-url')
      if (res.status === HttpStatusCode.NotFound) {
        return null
      }
      const websocket = await parsedResponse<IAPIAliveWebSocket>(res)
      return websocket.url
    } catch (e) {
      log.warn(`Alive web socket request failed: ${e}`)
      throw e
    }
  }
```

**File:** app/src/lib/http.ts (L57-76)
```typescript
/**
 * Deserialize the HTTP response body into an expected object shape
 *
 * Note: this doesn't validate the expected shape, and will only fail if it
 * encounters invalid JSON.
 */
async function deserialize<T>(response: Response): Promise<T> {
  try {
    const json = await response.json()
    return json as T
  } catch (e) {
    const contentLength = response.headers.get('Content-Length') || '(missing)'
    const requestId = response.headers.get('X-GitHub-Request-Id') || '(missing)'
    log.warn(
      `deserialize: invalid JSON found at '${response.url}' - status: ${response.status}, length: '${contentLength}' id: '${requestId}'`,
      e
    )
    throw e
  }
}
```

**File:** app/src/lib/stores/alive-store.ts (L158-195)
```typescript
  private async createSessionForAccount(
    account: Account
  ): Promise<IAliveEndpointSession | null> {
    const session = this.sessionForAccount(account)
    if (session !== undefined) {
      return session
    }

    const api = API.fromAccount(account)
    let webSocketUrl = null

    try {
      webSocketUrl = await api.getAliveWebSocketURL()
    } catch (e) {
      log.error(`Could not get Alive web socket URL for '${account.login}'`, e)
      return null
    }

    if (webSocketUrl === null) {
      return null
    }

    const aliveSession = new AliveSession(
      webSocketUrl,
      () => api.getAliveWebSocketURL(),
      false,
      this.notify
    )

    const newSession = {
      session: aliveSession,
      webSocketUrl,
    }

    this.sessionPerEndpoint.set(account.endpoint, newSession)

    return newSession
  }
```

**File:** app/src/main-process/alive-origin-filter.ts (L1-35)
```typescript
import { OrderedWebRequest } from './ordered-webrequest'

/**
 * Installs a web request filter to override the default Origin used to connect
 * to Alive web sockets
 */
export function installAliveOriginFilter(orderedWebRequest: OrderedWebRequest) {
  orderedWebRequest.onBeforeSendHeaders.addEventListener(async details => {
    const { protocol, host } = new URL(details.url)

    // Here we're only interested in WebSockets
    if (protocol !== 'wss:') {
      return {}
    }

    // Alive URLs are supposed to be prefixed by "alive" and then the hostname
    if (
      !/^alive\.github\.com$/.test(host) &&
      !/^alive\.(.*)\.ghe\.com$/.test(host)
    ) {
      return {}
    }

    // We will just replace the `alive` prefix (which indicates the service)
    // with `desktop`.
    return {
      requestHeaders: {
        ...details.requestHeaders,
        Origin: `https://${host.replace('alive.', 'desktop.')}`,
      },
    }
  })
}


```
