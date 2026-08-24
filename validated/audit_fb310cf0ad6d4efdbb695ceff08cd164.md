### Title
Permanent "Always Trust" certificate decisions cannot be revoked from within GitHub Desktop - (File: `app/src/main-process/app-window.ts`)

### Summary
When GitHub Desktop encounters an untrusted/invalid TLS certificate (e.g. from a spoofed or MITM'd Git/API endpoint), it shows the `UntrustedCertificate` dialog and, if the user clicks through, calls the OS-native `showCertificateTrustDialog`, which permanently adds the certificate to the operating system's trust store. There is no code path in Desktop that lists previously-trusted certificates or lets the user revoke that trust decision later, mirroring the `approvedHashes`-with-no-`revokeHash` pattern from the external report: an approval primitive exists, but no corresponding revocation primitive does.

### Finding Description
The renderer listens for `certificate-error` IPC events and shows a popup unless the URL is currently suppressed: [1](#0-0) 

That popup is `UntrustedCertificate`, which explicitly warns the user that continuing is unsafe and that "attackers might be trying to steal your data," but its only actions are "cancel" or "View/Add certificate": [2](#0-1) 

If the user chooses to continue, `onContinueWithUntrustedCertificate` calls `showCertificateTrustDialog` over IPC: [3](#0-2) 

In the main process, this invokes Electron's (untyped) native `dialog.showCertificateTrustDialog`, which — once the user picks "Always Trust" in the OS-native prompt — writes the certificate into the operating system trust store (macOS Keychain / Windows Cert Store): [4](#0-3) 

The `certificate-error` handler in the main process always denies the raw Chromium connection and forwards to the renderer to decide, but nothing in this flow — nor anywhere else in the codebase — records *which* certificates were trusted, exposes a "Manage trusted certificates" UI, or offers a way to undo/revoke a previous "Always Trust" decision from within Desktop: [5](#0-4) 

The only related state is an in-memory, per-URL suppression set used to avoid re-prompting during a single retry, which is unrelated to the underlying OS trust decision and is not a revocation mechanism: [6](#0-5) 

This is the direct analog of `approvedHashes[user][hash] = true` with no `revokeHash`: Desktop provides a one-way trust-grant action (`showCertificateTrustDialog`) with no counterpart to inspect or revoke it. If a user is momentarily convinced to click through (e.g., an attacker interposes on a corporate/public network, or spoofs a GitHub Enterprise hostname the user believes is legitimate, per the caveat already documented for that same dialog: "If this is a GitHub Enterprise trial" / "unusual top-level domain"), the trust decision becomes permanent at the OS level for that certificate, and the user has no in-app way to discover or undo it later even after realizing the certificate was fraudulent.

### Impact Explanation
An attacker who can present a fraudulent certificate for a domain the user interacts with through Desktop (MITM proxy, spoofed GHE hostname, DNS/ARP-level interception) can trick the user into permanently trusting that certificate. All subsequent connections from Desktop (and the OS at large, since this is written to the system trust store) to endpoints using that certificate will silently succeed without further warning, enabling credential/token interception on Git and API traffic and undetected tampering with pushed/fetched data — with no recovery path inside Desktop once the mistake is recognized.

### Likelihood Explanation
Requires the attacker to control a network path or spoofable endpoint the user connects to via Desktop (fits the "attacker controls a git remote/proxy response" criterion) and requires the user to click through a warning dialog once. Given the dialog explicitly acknowledges legitimate reasons to click through (GHE trials, unusual TLDs), some social pressure toward clicking through already exists, but this still needs one non-privileged user action, making likelihood moderate rather than trivial.

### Recommendation
Track certificates that the user chooses to trust via `showCertificateTrustDialog` (host + certificate fingerprint) in Desktop's own persisted settings, expose them in Preferences with a "Forget"/"Revoke" action, and re-prompt (or hard-block) connections to a host whose previously trusted certificate has changed, similar to how `revokeHash` was recommended to let a user undo a stale/malicious signature approval.

### Proof of Concept
1. Attacker intercepts a connection to a host the user has configured in Desktop (e.g. a GitHub Enterprise remote or a MITM proxy) and presents a spoofed/invalid TLS certificate.
2. Desktop's `certificate-error` handler fires and the `UntrustedCertificate` dialog is shown (`app/src/ui/app.tsx:361-371`).
3. User clicks "Add certificate", triggering `onContinueWithUntrustedCertificate` → `showCertificateTrustDialog` (`app/src/ui/app.tsx:1528-1535`, `app/src/main-process/app-window.ts:368-380`), which permanently adds the certificate to the OS trust store.
4. The user later suspects (or is informed) the certificate was fraudulent. There is no menu item, settings page, or dispatcher/IPC call anywhere in the codebase to list or remove this trust decision from within Desktop — the only remediation is manually editing the OS certificate/keychain store outside of the app.

### Citations

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

**File:** app/src/ui/untrusted-certificate/untrusted-certificate.tsx (L44-72)
```typescript
      >
        <DialogContent>
          <p>
            GitHub Desktop cannot verify the identity of {host}. The certificate
            ({this.props.certificate.subjectName}) is invalid or untrusted.{' '}
            <strong>
              This may indicate attackers are trying to steal your data.
            </strong>
          </p>
          <p>In some cases, this may be expected. For example:</p>
          <ul>
            <li>If this is a GitHub Enterprise trial.</li>
            <li>
              If your GitHub Enterprise instance is run on an unusual top-level
              domain.
            </li>
          </ul>
          <p>
            If you are unsure of what to do, cancel and contact your system
            administrator.
          </p>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup
            destructive={true}
            okButtonText={__DARWIN__ ? 'View Certificate' : 'Add certificate'}
          />
        </DialogFooter>
      </Dialog>
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
