Based on the investigation, the closest analog to the M-9 pattern ("trust decision issued once, no expiration, cannot be revoked except by an out-of-band action") is GitHub Desktop's certificate-error suppression cache.

### Title
Certificate-error trust suppression has no expiration and is keyed only by URL, not by the actual certificate - (File: `app/src/lib/suppress-certificate-error.ts`)

### Summary
When Desktop encounters a TLS certificate error for a request (e.g. to a GitHub Enterprise endpoint or any HTTPS remote reached through the app), it shows an `UntrustedCertificate` dialog and, once the user chooses to continue, records the decision in an in-memory `Set<string>` keyed solely by the request URL string, with no expiration and no binding to the actual certificate that was approved. [1](#0-0) 

### Finding Description
The suppression cache stores only the URL:
```
const suppressedUrls = new Set<string>()
export function suppressCertificateErrorFor(url: string) { suppressedUrls.add(url) }
export function isCertificateErrorSuppressedFor(url: string) { return suppressedUrls.has(url) }
``` [1](#0-0) 

The renderer's `certificate-error` IPC handler consults this cache before deciding whether to re-prompt the user:
```
ipcRenderer.on('certificate-error', (_, certificate, error, url) => {
  if (isCertificateErrorSuppressedFor(url)) {
    return
  }
  this.props.dispatcher.showPopup({ type: PopupType.UntrustedCertificate, certificate, url })
})
``` [2](#0-1) 

This mirrors the Sherlock finding's broken invariant: a trust artifact ("signature"/approval) is accepted **once** and then treated as permanently valid for that key (here, the URL string) with **no expiration timestamp and no binding to the specific certificate fingerprint that was actually approved**. Just as the Factory contract only checked `signedOnly(...)` without an expiry, this cache only checks `suppressedUrls.has(url)` without checking whether the certificate currently being presented for that URL is the same one the user reviewed. A network attacker or malicious proxy sitting on the same URL (e.g., a compromised Wi-Fi, corporate proxy, or DNS-hijacked GHE hostname) can present a *different, attacker-controlled* certificate after the user has approved one cert for that URL, and Desktop will silently accept it for the remainder of the process lifetime because the suppression check never re-validates the certificate itself — only the URL string.

The only way to "revoke" this trust is to restart the application (which clears the in-memory `Set`), which is directly analogous to the audit's remediation path of "revoke SIGNER_ROLE / rotate the key" — an indirect, coarse-grained, and non-obvious mechanism to undo a decision that should have had a natural expiration or should have been bound to the certificate's identity.

### Impact Explanation
If exploited, this allows silent acceptance of a subsequently swapped-in malicious TLS certificate for a host the user already trusted once, enabling a MITM to intercept authenticated GitHub Enterprise/API traffic (credentials, tokens, repository data) for the remainder of the Desktop session without any further user prompt. This is a credential/token exfiltration and integrity risk consistent with the report's "silently persists trust beyond intended scope" theme.

### Likelihood Explanation
Exploitation requires the attacker to control network traffic to the same URL after a legitimate self-signed/untrusted-cert prompt has already been approved once (e.g., enterprise self-signed certs are common, making a first approval routine) — this is a realistic on-path/network-attacker scenario, not local access or malware. However, I was not able to fully confirm every call site of `suppressCertificateErrorFor` (3 references exist in `app/src/lib/api.ts` that I could not fully inspect in this session) or verify whether Electron's separate OS-level `showCertificateTrustDialog` (which offers a stronger, cert-bound "Always trust") is the exclusive path used before `suppressCertificateErrorFor` is invoked, or whether it's invoked independently in a lighter-weight bypass. This should be verified against the full contents of `app/src/lib/api.ts` and `app/src/main-process/main.ts`'s `show-certificate-trust-dialog` handling before treating this as a fully confirmed, exploitable path. [3](#0-2) 

### Recommendation
Bind the suppression decision to the actual certificate fingerprint (not just the URL string), and/or add an expiration/TTL so a stale trust decision cannot persist indefinitely across the session; re-validate the fingerprint on every subsequent certificate error for that URL rather than checking URL membership alone.

### Proof of Concept
1. Connect Desktop to a GHE-like HTTPS endpoint presenting a self-signed certificate A; approve the `UntrustedCertificate` dialog, which calls `suppressCertificateErrorFor(url)`.
2. Without restarting Desktop, have the network path (attacker-controlled proxy/MITM) swap in a different, attacker-issued certificate B for the same `url`.
3. Desktop's `certificate-error` handler calls `isCertificateErrorSuppressedFor(url)`, finds it in the `Set`, and returns without re-prompting — the connection using certificate B is silently accepted. [2](#0-1) [1](#0-0)

### Citations

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

**File:** app/src/main-process/main.ts (L564-571)
```typescript
  ipcMain.on('show-certificate-trust-dialog', (_, certificate, message) => {
    // This API is only implemented for macOS and Windows right now.
    if (__DARWIN__ || __WIN32__) {
      onDidLoad(window => {
        window.showCertificateTrustDialog(certificate, message)
      })
    }
  })
```
