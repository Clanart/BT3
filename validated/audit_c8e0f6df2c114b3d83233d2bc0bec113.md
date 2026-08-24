## Finding confirmed — no host/endpoint validation on `bypassURL`

### Title
Unvalidated `bypassURL` extracted from git remote stderr is opened via `shell.openExternal` without checking it belongs to the pushed repository's endpoint - (File: `app/src/ui/dispatcher/error-handlers.ts`)

### Summary
`extractSecretScanningResults` parses the `bypassURL` group purely with a permissive regex (`https[\s\S]*?` up to the next whitespace) and never checks it against the endpoint of `e.metadata.repository`. That raw string is stored on `ISecretScanResult.bypassURL` and rendered directly as a clickable `LinkButton`, which on click calls `shell.openExternal(uri)` with zero validation.

### Finding Description
`getRemoteMessage` (line 704) extracts every line beginning with `remote: ` from git's stderr — content fully controlled by whatever the configured remote (or a MITM/proxy for non-TLS transports) returns. [1](#0-0) 

`extractSecretScanningResults` then matches a `bypassURL` group using a wildcard regex that accepts any `https...` token up to the next whitespace character, with no scheme/host allow-list and no comparison to the target repository's endpoint: [2](#0-1) [3](#0-2) 

`secretScanningPushProtectionErrorHandler` never cross-checks `secrets[].bypassURL` against `e.metadata.repository.gitHubRepository.endpoint` before handing it to the popup: [4](#0-3) 

The dialog renders `secret.bypassURL` as the `uri` of a `LinkButton`: [5](#0-4) 

`LinkButton.onClick` performs no URL validation whatsoever and passes the string straight to `shell.openExternal`: [6](#0-5) 

So the chain is: attacker-controlled git server stderr → regex-extracted `bypassURL` → rendered link → `shell.openExternal(attackerURL)`, with no check anywhere that the host matches `api.github.com`/the endpoint of the repository being pushed to (`e.metadata.repository`).

### Impact Explanation
This lets a malicious or compromised git remote fully control which URL is opened in the user's default OS browser when they click "Bypass." Since the URL is opened in the user's real, already-authenticated browser session, this is a classic open-redirect/spoofing primitive that can be used for GitHub credential phishing (a convincing look-alike domain, or an actual GitHub OAuth-authorize deep link crafted to bind the victim's account to an attacker app) presented as a trusted "GitHub secret scanning bypass" action. It is not, however, an automatic silent exfiltration of session cookies/tokens purely by opening the browser — `shell.openExternal` just opens a tab; actual credential/token loss requires the user to additionally act on the spoofed page (e.g., enter credentials or approve an OAuth grant), which is a meaningful caveat versus the "exfiltrates session context" framing in the question.

### Likelihood Explanation
Requires only that the user push to a repository whose remote (or a MITM on the transport) is attacker-controlled/malicious and returns a crafted `PushWithSecretDetected` stderr payload — no local access or prior compromise needed, matching the in-scope threat model. The user must then click the "Bypass" link, which is a natural, expected action within the flow (not an unnatural social-engineering step) since the whole dialog UX is designed around clicking that exact link.

### Recommendation
Before rendering/opening `bypassURL`, validate that its origin matches the expected GitHub endpoint for `e.metadata.repository.gitHubRepository.endpoint` (e.g., `api.github.com`/the enterprise host, and the expected `/security/...` or `/settings/security_analysis/...` path). Reject/strip any `bypassURL` whose host doesn't match before constructing `ISecretScanResult`, and consider having `LinkButton`/`shell.openExternal` enforce an allow-list for security-sensitive dialogs like this one.

### Proof of Concept
1. Push a commit containing a fake secret to a repository configured on endpoint A (e.g. `github.com`).
2. Have the test git server return git push output with `PushWithSecretDetected` and a `remote: ` block whose `bypassURL` is `https://attacker.example.com/phish` (or a real-but-different endpoint B's URL, e.g. an OAuth authorize link for a different account).
3. Observe `extractSecretScanningResults` accepts it unmodified (line 654-660) and `secretScanningPushProtectionErrorHandler` shows the `PushProtectionError` popup with that URL (lines 690-693) with no host check against endpoint A.
4. Click "Bypass" in `PushProtectionErrorDialog` → `LinkButton.onClick` → `shell.openExternal` opens the attacker-chosen URL in the user's browser, unrelated to the repository's actual endpoint.

### Citations

**File:** app/src/ui/dispatcher/error-handlers.ts (L616-620)
```typescript
function extractSecretScanningResults(
  remoteMessage: string
): ReadonlyArray<ISecretScanResult> {
  const secretsRegex =
    /—— (?<description>.*?) —+[\s\S]*?locations:(?<locationsGroup>(?:\s+- commit: [a-f0-9]{40}\s+path: [\s\S]*?)+).*?(?<bypassURL>https[\s\S]*?) /g
```

**File:** app/src/ui/dispatcher/error-handlers.ts (L654-660)
```typescript
    secrets.push({
      id: bypassURL.split('/').pop() || '',
      description,
      bypassURL,
      locations,
      requiresApproval: !!match.at(0)?.includes('request an exemption'),
    })
```

**File:** app/src/ui/dispatcher/error-handlers.ts (L684-693)
```typescript
  const remoteMessage = getRemoteMessage(coerceToString(gitError.result.stderr))
  const secrets = extractSecretScanningResults(remoteMessage)

  dispatcher.incrementMetric('pushBlockedBySecretScanningCount')
  dispatcher.incrementMetric('secretsDetectedOnPushCount', secrets.length)

  dispatcher.showPopup({
    type: PopupType.PushProtectionError,
    secrets,
  })
```

**File:** app/src/ui/dispatcher/error-handlers.ts (L704-711)
```typescript
function getRemoteMessage(stderr: string) {
  const needle = 'remote: '

  return stderr
    .split(/\r?\n/)
    .filter(x => x.startsWith(needle))
    .map(x => x.substring(needle.length))
    .join('\n')
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
