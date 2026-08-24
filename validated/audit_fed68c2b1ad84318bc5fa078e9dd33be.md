### Title
Attacker-controlled remote push output is parsed and turned into a clickable link opened via `shell.openExternal` without validation - (File: `app/src/ui/dispatcher/error-handlers.ts`)

### Summary
GitHub Desktop parses free-form text from the `remote: ` lines of `git push` stderr output — content fully controlled by whatever server (or MITM/impersonator) answers the push — and extracts a `bypassURL` field via regex. That value is handed unmodified to a `LinkButton` that calls `shell.openExternal(uri)` when clicked, with no validation that the URL actually points to `github.com`. This is a direct structural analog of the reported bug class: text from an untrusted remote endpoint is reflected into a UI action instead of being treated as opaque, hostile data.

### Finding Description
When a `git push` is rejected with `DugiteError.PushWithSecretDetected`, `secretScanningPushProtectionErrorHandler` extracts the "remote: " prefixed lines from stderr: [1](#0-0) 

That text is then parsed with a regex to build `ISecretScanResult` objects, including a `bypassURL` captured as `https[\s\S]*?` up to the first whitespace — i.e. any attacker-chosen byte sequence beginning with the literal string `https`, not a validated URL: [2](#0-1) 

The handler then shows a popup with these attacker-influenced `secrets`: [3](#0-2) 

In the dialog, `secret.bypassURL` is passed directly as the `uri` prop of a `LinkButton`: [4](#0-3) 

`LinkButton` performs no validation of the URI before calling `shell.openExternal`: [5](#0-4) 

The broken invariant is the same as in the JSON-RPC report: server/attacker-supplied text is trusted to be well-formed, benign UI content, when in fact anything that can influence git's stderr on a push (a malicious/compromised remote, a repo hosting service impersonator, or a MITM on the git transport) fully controls the string that ends up as a clickable "Bypass" action in the Desktop UI. No allow-listing of host (`github.com`) or scheme is performed anywhere in this path.

### Impact Explanation
If an attacker controls the git remote/server a user pushes to (self-hosted Git server, compromised GitHub Enterprise instance, or a MITM that can inject text resembling GitHub's secret-scanning push-protection response into stderr), they can craft a `remote: ` output that satisfies the `extractSecretScanningResults` regex and substitute an arbitrary attacker-controlled URL for `bypassURL`. The victim, believing the "Bypass" button is a legitimate GitHub Secret Scanning bypass link, clicks it, and Desktop calls `shell.openExternal` on the attacker's URL. This can be used for credential-phishing (spoofing a GitHub login/bypass page) or to trigger any URL-scheme-based exploit chain the OS's `shell.openExternal` is vulnerable to (a known class of Electron/OS issues where attacker-controlled schemes/paths passed to `openExternal` lead to unexpected native handler invocation). This mirrors the Electrum/ZecWallet precedent cited in the original report almost exactly — untrusted network peer text becomes a trusted-looking user action.

### Likelihood Explanation
No local access, admin rights, or pre-existing malware is required — only that the user pushes to a remote controlled or intercepted by the attacker, which is squarely within GitHub Desktop's threat model (arbitrary remotes/git hosts). The regex is permissive (`https[\s\S]*?` up to first whitespace, not an actual URL grammar or host check), so crafting a convincing-looking `remote: ` payload that matches `secretsRegex` and substitutes a bypass URL is straightforward once the surrounding structure (`—— description —+ ... locations: ...`) is reproduced.

### Recommendation
- Do not render `bypassURL` (or any other field parsed from `remote:` push output) as a clickable link without validating that it is a well-formed absolute URL whose origin is the expected GitHub host (`github.com`/the repository's configured enterprise host).
- Reject or neutralize the popup entirely if the parsed `bypassURL` fails validation, rather than silently trusting it.
- More generally, apply the same "never inject remote-controlled text directly into actionable UI elements" principle to all `remote:`-derived parsing in `error-handlers.ts` (e.g. `getRemoteMessage`, `samlReauthErrorMessageRe`), and add output-encoding/allow-listing at the point content crosses from "git process output" into "UI action," not just at the point it becomes display text.

### Proof of Concept
1. Attacker controls (or MITMs) a git remote that the victim pushes to.
2. On `git push`, the attacker's Git server returns a rejection with exit behavior matching `DugiteError.PushWithSecretDetected`, and stderr containing lines such as:
   ```
   remote: —— Fake Secret ——————————
   remote:   locations:
   remote:     - commit: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
   remote:       path: config.js:1
   remote: https://attacker.example.com/phish-login 
   ```
3. `getRemoteMessage` strips the `remote: ` prefix, and `extractSecretScanningResults` matches the payload, setting `bypassURL = "https://attacker.example.com/phish-login"`.
4. `secretScanningPushProtectionErrorHandler` shows `PopupType.PushProtectionError` with this secret.
5. `PushProtectionErrorDialog` renders a "Bypass" `LinkButton` with `uri="https://attacker.example.com/phish-login"`.
6. The victim, trusting the GitHub Desktop UI, clicks "Bypass"; `LinkButton.onClick` calls `shell.openExternal("https://attacker.example.com/phish-login")`, opening the attacker's page in the system browser with no indication it was not an actual GitHub Secret Scanning URL.

### Citations

**File:** app/src/ui/dispatcher/error-handlers.ts (L616-663)
```typescript
function extractSecretScanningResults(
  remoteMessage: string
): ReadonlyArray<ISecretScanResult> {
  const secretsRegex =
    /—— (?<description>.*?) —+[\s\S]*?locations:(?<locationsGroup>(?:\s+- commit: [a-f0-9]{40}\s+path: [\s\S]*?)+).*?(?<bypassURL>https[\s\S]*?) /g

  const matches = [...remoteMessage.matchAll(secretsRegex)]

  const secrets: Array<ISecretScanResult> = []
  if (matches.length === 0) {
    return secrets
  }

  for (const match of matches) {
    if (match.groups === undefined || match.index === undefined) {
      continue
    }

    const { bypassURL, description, locationsGroup } = match.groups

    const locationsRegex =
      /- commit: (?<commitSha>[a-f0-9]{40})\s+path: (?<path>.*?):(?<lineNumber>\d+)/g
    const locationMatches = [...locationsGroup.matchAll(locationsRegex)]

    const locations: ISecretLocation[] = []

    locationMatches.forEach(locationMatch => {
      if (locationMatch.groups === undefined) {
        return
      }
      const { commitSha, path, lineNumber } = locationMatch.groups
      locations.push({
        commitSha,
        path,
        lineNumber: parseInt(lineNumber, 10),
      })
    })

    secrets.push({
      id: bypassURL.split('/').pop() || '',
      description,
      bypassURL,
      locations,
      requiresApproval: !!match.at(0)?.includes('request an exemption'),
    })
  }

  return secrets
```

**File:** app/src/ui/dispatcher/error-handlers.ts (L670-696)
```typescript
export async function secretScanningPushProtectionErrorHandler(
  error: Error,
  dispatcher: Dispatcher
) {
  const e = asErrorWithMetadata(error)
  if (!e) {
    return error
  }

  const gitError = asGitError(e.underlyingError)
  if (gitError?.result.gitError !== DugiteError.PushWithSecretDetected) {
    return error
  }

  const remoteMessage = getRemoteMessage(coerceToString(gitError.result.stderr))
  const secrets = extractSecretScanningResults(remoteMessage)

  dispatcher.incrementMetric('pushBlockedBySecretScanningCount')
  dispatcher.incrementMetric('secretsDetectedOnPushCount', secrets.length)

  dispatcher.showPopup({
    type: PopupType.PushProtectionError,
    secrets,
  })

  return null
}
```

**File:** app/src/ui/dispatcher/error-handlers.ts (L698-713)
```typescript
/**
 * Extract lines from Git's stderr output starting with the
 * prefix `remote: `. Useful to extract server-specific
 * error messages from network operations (fetch, push, pull,
 * etc).
 */
function getRemoteMessage(stderr: string) {
  const needle = 'remote: '

  return stderr
    .split(/\r?\n/)
    .filter(x => x.startsWith(needle))
    .map(x => x.substring(needle.length))
    .join('\n')
}

```

**File:** app/src/ui/secret-scanning/push-protection-error-dialog.tsx (L140-150)
```typescript
  private renderBypassButton = (secret: ISecretScanResult) => {
    if (secret.requiresApproval) {
      return (
        <LinkButton
          ariaLabel={`Bypass ${secret.description}`}
          uri={secret.bypassURL}
          onClick={this.props.onDelegatedBypassLinkClick}
        >
          Bypass
        </LinkButton>
      )
```

**File:** app/src/ui/lib/link-button.tsx (L76-92)
```typescript
  private onClick = (event: React.MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()

    if (this.props.disabled) {
      return
    }

    const uri = this.props.uri
    if (uri) {
      shell.openExternal(uri)
    }

    const onClick = this.props.onClick
    if (onClick) {
      onClick()
    }
  }
```
