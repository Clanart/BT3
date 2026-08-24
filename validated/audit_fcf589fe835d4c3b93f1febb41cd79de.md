## Analysis

The MetaMask report's broken invariant is: **a low-level RPC surface grants access to a security-sensitive secret (all addresses) based on a coarse check (root-key access) instead of the fine-grained, per-origin consent the wallet's own UI enforces.**

The closest analog in this codebase is in `app/src/main-process/authenticated-image-filter.ts`, which attaches a signed-in account's OAuth/PAT token to arbitrary GHES/GHE requests based on **origin + URL-shape matching alone**, with no verification that the resource actually belongs to the repository the user is currently viewing or has "connected" that identity to.### Title
Origin-only matching in the authenticated image filter attaches a signed-in account's token to attacker-chosen GHE/GHES asset paths regardless of repository consent - ([File: app/src/main-process/authenticated-image-filter.ts])

### Summary
GitHub Desktop's account model is meant to scope credentials to the account/repository the user actually signed in for. `installAuthenticatedImageFilter` breaks that scoping: it attaches a stored account's `Authorization` header to *any* outgoing request whose **origin** matches a signed-in account's endpoint and whose **path** loosely matches a regex for "repo asset" or "enterprise avatar" URLs — with no check on which repository, PR, or account context originated the render. This mirrors the MetaMask report's core flaw: a low-level mechanism (origin+regex matching) exposes a security-sensitive secret (the account token) without the fine-grained, per-repository consent that the rest of the application enforces elsewhere.

### Finding Description
`installAuthenticatedImageFilter` builds a `Map<origin, token>` from all signed-in accounts and, on every `onBeforeSendHeaders` event fired for **all requests in the default Electron session**, attaches `Authorization: token <token>` whenever:
1. the request's `origin` equals a stored account's endpoint origin, **and**
2. the request's `pathname` matches `isEnterpriseAvatarPath` (`/api/v3/enterprise/avatars/...`) or `isGitHubRepoAssetPath` (`/^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/` or `/^\/user-attachments\/assets\/[^/]+\/?$/`). [1](#0-0) [2](#0-1) 

These regex path shapes describe the *generic* GitHub "attachment/asset" upload URL format — they do not reference any specific repository, owner, or PR that the request should be scoped to. Any content an attacker controls that is rendered inside GitHub Desktop and can embed an `<img src="...">` — e.g., a PR body, issue comment, commit message, or release note pulled from a GitHub Enterprise instance the victim is signed into — is rendered via `SandboxedMarkdown`, which sanitizes markdown with DOMPurify/`marked` but still permits `<img>` elements and loads them from the *same* Electron `session.defaultSession`, so the request still passes through `OrderedWebRequest`/`installAuthenticatedImageFilter`. [3](#0-2) [4](#0-3) 

Because matching is done purely by origin + path *shape*, an attacker who controls (or can upload an attachment to) **any** repository/issue on the same GHES/GHE host as the victim's signed-in account can craft an asset URL of the required shape and have Desktop silently attach the victim's persistent account token to the request the moment that content is rendered anywhere in the app (PR view, PR quick view, commit view, notifications) — with no relation to the repository the token is "supposed" to be used for and no user consent step, unlike the deliberate "sign in" / "select account for repository" flows Desktop otherwise uses (`AccountsStore`, `AccountPicker`). [5](#0-4) 

### Impact Explanation
The token exposed is a full account token/PAT used for the GitHub Enterprise API, not a narrowly-scoped avatar token. `same-origin-filter.ts` prevents this Authorization header from being forwarded to a *different* origin on redirect, so the token itself cannot be trivially exfiltrated off-host by this mechanism alone — but the underlying invariant break (fine-grained "connect this token to this repository/content" consent) is exactly what the MetaMask report criticizes: the app's credential-scoping is based on a coarse pattern match rather than an explicit account/repository binding decided by the user. This can be leveraged to:
- Force the victim's GHES token to authenticate requests to arbitrary repos/attachments on that instance triggered purely by rendering attacker-supplied content, letting an attacker probe/confirm private-repo attachment existence or access via image load/error side channels.
- Undermine the intended one-account-per-endpoint boundary since the token is applied session-wide to any matching path, not scoped to the specific repository context that a piece of rendered markdown came from.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to be signed into a GHES/GHE account and to view rendered markdown (PR/issue/commit body, notification, release notes) containing an attacker-influenced `<img>` URL matching the generic asset-path shape on that same host — a realistic scenario since PR/issue bodies routinely embed such GitHub-hosted attachment URLs and are rendered automatically when browsing pull requests. No special privileges, local access, or user action beyond normal PR/issue browsing are required.

### Recommendation
Scope the authorization decision to the actual repository/account context of the content being rendered rather than origin+regex alone — e.g., pass the originating repository/account through `OrderedWebRequest` details (or a per-webContents/tag context) and only attach the token when the asset path's owner/repo matches a repository the active account is actually authorized against, or restrict the filter to requests explicitly issued by Desktop's own asset-fetch code path rather than any markdown-rendered `<img>` in the shared session.

### Proof of Concept
1. Sign into a GitHub Enterprise Server account in Desktop (`ghes.example.com`).
2. As an attacker with any account on `ghes.example.com`, upload an attachment to any issue/PR (even a public, unrelated repo) to obtain a URL of the form `https://ghes.example.com/attacker-owner/attacker-repo/assets/<userid>/<guid>` or `https://ghes.example.com/user-attachments/assets/<guid>`.
3. Get the victim to view a PR/issue/commit whose body/comment (attacker-authored, e.g. in a repo they collaborate on) embeds `![x](https://ghes.example.com/attacker-owner/attacker-repo/assets/<userid>/<guid>)`.
4. When `SandboxedMarkdown` renders the `<img>`, Desktop's main process automatically attaches `Authorization: token <victim-token>` to that request because origin+path match, per `installAuthenticatedImageFilter` — regardless of the repository actually open or the ownership of the asset. [6](#0-5) 

Note: I was not able to fully trace whether Desktop's `same-origin-filter` or GHES server-side authorization would fully neutralize downstream exfiltration in every deployment configuration; a background Devin session with the full codebase/test harness would be needed to confirm end-to-end exploitability (e.g., via response side-channels) beyond the token-attachment logic itself, which is clearly demonstrated by the static code above.

### Citations

**File:** app/src/main-process/authenticated-image-filter.ts (L1-16)
```typescript
import { getDotComAPIEndpoint, getHTMLURL } from '../lib/api'
import { EndpointToken } from '../lib/endpoint-token'
import { OrderedWebRequest } from './ordered-webrequest'

function isEnterpriseAvatarPath(pathname: string) {
  return pathname.startsWith('/api/v3/enterprise/avatars/')
}

function isGitHubRepoAssetPath(pathname: string) {
  // Matches paths like: /repo/owner/assets/userID/guid
  return (
    /^\/[^/]+\/[^/]+\/assets\/[^/]+\/[^/]+\/?$/.test(pathname) ||
    // or: /user-attachments/assets/guid
    /^\/user-attachments\/assets\/[^/]+\/?$/.test(pathname)
  )
}
```

**File:** app/src/main-process/authenticated-image-filter.ts (L26-48)
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
```

**File:** app/src/ui/lib/sandboxed-markdown.tsx (L126-141)
```typescript

  public renderMarkdown = async () => {
    const { markdown } = this.props

    const body = DOMPurify.sanitize(
      marked(markdown, {
        // https://marked.js.org/using_advanced  If true, use approved GitHub
        // Flavored Markdown (GFM) specification.
        gfm: true,
        // https://marked.js.org/using_advanced, If true, add <br> on a single
        // line break (copies GitHub behavior on comments, but not on rendered
        // markdown files). Requires gfm be true.
        breaks: true,
      })
    )

```

**File:** app/src/main-process/main.ts (L337-350)
```typescript
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
```

**File:** app/src/ui/account-picker.tsx (L34-60)
```typescript
interface IAccountListItem extends IFilterListItem {
  readonly id: string
  readonly text: ReadonlyArray<string>
  readonly account: Account
}

const getItemId = (account: Account) => `${account.login}@${account.endpoint}`

/**
 * A select-like element for filter and selecting an account.
 */
export class AccountPicker extends React.Component<
  IAccountPickerProps,
  IAccountPickerState
> {
  private getFilterListGroups = memoizeOne(
    (
      accounts: ReadonlyArray<Account>
    ): ReadonlyArray<IFilterListGroup<IAccountListItem>> => [
      {
        identifier: 'accounts',
        items: accounts.map(account => ({
          text: [account.login, account.endpoint],
          id: getItemId(account),
          account,
        })),
      },
```
