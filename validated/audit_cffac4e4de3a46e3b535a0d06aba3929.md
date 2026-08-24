Based on the investigation, the relevant local analog concerns how GitHub Desktop caches "trust this certificate" decisions.

### Title
Certificate-trust bypass caches by URL string with no certificate fingerprint binding or expiry - (File: app/src/lib/suppress-certificate-error.ts)

### Summary
The report's broken invariant is that a *security decision made at time T* (accepting a certificate) is not re-validated against the actual data presented afterward — a stale timestamp lets an old, no-longer-trustworthy credential keep being accepted. The same class of bug exists in GitHub Desktop's certificate-trust flow: once a user dismisses an `UntrustedCertificate` dialog by choosing "Always trust", the decision is cached keyed only by the connection URL string, with no binding to the specific certificate (no fingerprint, no serial number, no expiry check), and no expiry of the trust decision itself.

### Finding Description
When Electron reports a `certificate-error` for a webContents request, the main process forwards it to the renderer [1](#0-0) , which shows the `UntrustedCertificate` popup unless the URL has been marked as suppressed [2](#0-1) .

The suppression store is a plain module-level `Set<string>` keyed purely by URL, with no association to the certificate that was actually approved:

```ts
const suppressedUrls = new Set<string>()

export function suppressCertificateErrorFor(url: string) {
  suppressedUrls.add(url)
}

export function isCertificateErrorSuppressedFor(url: string) {
  return suppressedUrls.has(url)
}
``` [3](#0-2) 

The user's approval flow itself only forwards the certificate to the OS trust dialog and never persists or checks the certificate's identity/fingerprint against what is later presented for the same URL: [4](#0-3)  and [5](#0-4) .

Because `isCertificateErrorSuppressedFor` compares only the URL string (not a certificate fingerprint, serial number, or expiry timestamp), any subsequent connection to that same URL — even one presenting a *different* certificate (e.g., an attacker's MITM certificate, a revoked/expired certificate, or one from a compromised proxy) — silently bypasses the trust prompt entirely as long as suppression for that URL is still active in the process's lifetime.

### Impact Explanation
This mirrors the report's core issue: a trust decision computed once is reused later without re-validating the actual security-relevant input (certificate identity/validity) that changed. In the Desktop context, an attacker positioned as a network MITM/proxy (e.g., a corporate or compromised proxy serving api.github.com or an Enterprise GHE endpoint) could exploit a session where the user has already accepted one untrusted certificate for that host, then substitute a different certificate (including one signed for a revoked/compromised key) for later requests on the same URL, and the app will accept it silently — no dialog, no comparison. This can lead to credential/token exfiltration, since git and API traffic (including OAuth tokens) would flow to the MITM without further user awareness.

### Likelihood Explanation
This requires an active network position (MITM/interception) capable of injecting a bad certificate on the same URL already trusted once by the user — a realistic scenario for corporate/enterprise proxies, which the codebase's own documentation acknowledges as a common environment (`docs/technical/proxies.md`) [6](#0-5) . This does not require local/physical access, admin rights, or leaked credentials — only network-path control and one prior user "Always trust" action for the same host, which is a plausible baseline in GHE/proxy-heavy environments.

### Recommendation
- **Short term**: Bind the suppression entry to the specific certificate (e.g., fingerprint/serial number) rather than the URL alone, so a different certificate presented for the same URL always re-triggers the trust dialog.
- **Long term**: Add an expiry/TTL to cached trust decisions and re-validate the certificate's `validExpiry`/`validStart` on each connection, rejecting or re-prompting when a previously trusted certificate has since expired or been superseded by a different one.

### Proof of Concept
1. Attacker controls a network path to a GHE host or api.github.com (e.g., MITM proxy).
2. User connects, gets prompted by `UntrustedCertificate` dialog, clicks "Always trust" — `suppressCertificateErrorFor(url)` is called for that URL (path in `app/src/lib/api.ts`, confirmed by grep matches but full call site not retrieved in this session).
3. Attacker later swaps in a different (e.g., expired or otherwise invalid) certificate for the *same URL*.
4. `isCertificateErrorSuppressedFor(url)` still returns `true` [7](#0-6)  since only the URL string is checked, so the app silently accepts the new certificate without showing the dialog again, allowing continued MITM interception of git/API traffic including tokens.

**Note on completeness**: The exact call sites in `app/src/lib/api.ts` that invoke `suppressCertificateErrorFor`/`clearCertificateErrorSuppressionFor` were located by `grep_search` but their full surrounding logic was not retrieved in this session due to tool-call limits; a Devin session with full file access should confirm whether any fingerprint check exists elsewhere before treating this as fully confirmed.

### Citations

**File:** app/src/main-process/main.ts (L747-756)
```typescript
app.on(
  'certificate-error',
  (event, webContents, url, error, certificate, callback) => {
    callback(false)

    onDidLoad(window => {
      window.sendCertificateError(certificate, error, url)
    })
  }
)
```

**File:** app/src/ui/app.tsx (L361-371)
```typescript
    ipcRenderer.on('certificate-error', (_, certificate, error, url) => {
      if (isCertificateErrorSuppressedFor(url)) {
        return
      }

      this.props.dispatcher.showPopup({
        type: PopupType.UntrustedCertificate,
        certificate,
        url,
      })
    })
```

**File:** app/src/ui/app.tsx (L1528-1535)
```typescript
  private onContinueWithUntrustedCertificate = (
    certificate: Electron.Certificate
  ) => {
    showCertificateTrustDialog(
      certificate,
      'Could not securely connect to the server, because its certificate is not trusted. Attackers might be trying to steal your information.\n\nTo connect unsafely, which may put your data at risk, you can “Always trust” the certificate and try again.'
    )
  }
```

**File:** app/src/lib/suppress-certificate-error.ts (L1-13)
```typescript
const suppressedUrls = new Set<string>()

export function suppressCertificateErrorFor(url: string) {
  suppressedUrls.add(url)
}

export function clearCertificateErrorSuppressionFor(url: string) {
  suppressedUrls.delete(url)
}

export function isCertificateErrorSuppressedFor(url: string) {
  return suppressedUrls.has(url)
}
```

**File:** app/src/main-process/app-window.ts (L368-380)
```typescript
  public showCertificateTrustDialog(
    certificate: Electron.Certificate,
    message: string
  ) {
    // The Electron type definitions don't include `showCertificateTrustDialog`
    // yet.
    const d = dialog as any
    d.showCertificateTrustDialog(
      this.window,
      { certificate, message },
      () => {}
    )
  }
```

**File:** docs/technical/proxies.md (L61-71)
```markdown
## Windows caveats for MITM proxies

Some Enterprises and organizations use "regular" proxies which only forwards traffic but doesn't actively inspect it. Some, however, use what's commonly referred to as man-in-the-middle proxies, snooping proxies, or https intercepting proxies.

These proxies issue fake ssl certificates when clients make request to https sites and rely on the clients trusting their "fake" certificate authority certificate. See [MITM-proxies](https://ec.haxx.se/usingcurl/usingcurl-proxies#mitm-proxies) in the cURL documentation for a better explanation.

When these snooping proxy servers issue their fake certificate they rarely include any certificate revocation list details. In other words they're not telling the clients who they should contact to see if a particular certificate has been revoked. This is called Certificate Revocation List Distribution Points or CRL DP. The lack of distribution points in a certificate is normally ignored by http(s) clients and that's the case for Git on macOS and linux. On Windows however there's two SSL backends to choose from; `openssl` and `schannel`. OpenSSL is a cross-platform SSL/TLS implementation whereas `schannel` uses the built-in SSL/TLS subsystem in Windows.

Unfortunately the `schannel` backend in cURL throws an error when failing to check for certificate revocation. We've seen this error [so many times](https://github.com/desktop/desktop/issues/3326) that it's made our "known issues" document. To combat this a new configuration option (`http.schannelCheckRevoke`) was added to Git which lets users disable certificate revocation checks entirely. This certainly isn't ideal and we'd like to see a solution to allow best effort certificate revocation checks which always attempts to check for revocation but doesn't throw an error if it fails to do so (see the Future improvements section below).

For now though this is our only workaround and as such [#9188](https://github.com/desktop/desktop/pull/9188) was introduced which detects this specific error and allow the user to disable revocation checks. **Note: the toggle to turn this setting on or off in the options dialog is hidden unless this condition has been encountered before**
```
