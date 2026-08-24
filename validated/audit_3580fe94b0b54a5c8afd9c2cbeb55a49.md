## Finding Assessment: Valid (with a caveat on full exploitability chain)

### Title
Certificate trust decision is not scoped to the presenting host — `onContinue` and `showCertificateTrustDialog` drop the URL/host entirely - (`app/src/ui/untrusted-certificate/untrusted-certificate.tsx`)

### Summary
The `UntrustedCertificate` dialog's continue handler and the main-process trust API it ultimately drives both operate purely on the `Electron.Certificate` object, with no host binding carried through to the actual trust decision.

### Finding Description
`UntrustedCertificate` receives both `certificate` and `url` as props (the `url` is used only to compute a `host` string for display in `render()`), but its `onContinue` prop type and the private handler that invokes it strip the host entirely: [1](#0-0) [2](#0-1) 

The `host` computed from `this.props.url` is used only for the user-facing warning text at render time, not passed onward: [3](#0-2) 

On the main-process side, `AppWindow.showCertificateTrustDialog(certificate, message)` — the sink that actually invokes Electron's native `dialog.showCertificateTrustDialog` — likewise takes no host/URL argument: [4](#0-3) 

Note that the *original* IPC message sent from main to renderer, `sendCertificateError`, does carry the `url` alongside the certificate: [5](#0-4) 

but that `url` is used in the renderer only for display, and is discarded before the trust decision is echoed back to the main process and passed into `dialog.showCertificateTrustDialog`. This confirms the exact claim in the proof idea: the `onContinue` prop signature never carries `url`/host, and the `showCertificateTrustDialog` call site is not passed a host to scope Electron's trust decision.

**Caveat:** I was unable to fully read `app/src/main-process/main.ts` within the available tool budget to confirm exactly how the "continue" IPC message is correlated back to the specific pending `certificate-error` event/callback (e.g., whether correlation is keyed by certificate fingerprint alone, or by some combination that still preserves per-connection scoping via closure over the original event). Electron's own `dialog.showCertificateTrustDialog` API is documented to add a certificate to the OS-level trust store rather than scoping trust to a specific hostname, which is consistent with the reported invariant violation, but I could not verify whether GitHub Desktop's main process performs any additional host-binding check before invoking that API or before resuming the underlying network request. This is a genuine gap in my verification, not a confirmed absence of mitigation.

### Impact Explanation
If, as the code strongly suggests, trust is registered against the certificate object alone (with no host binding carried from `UntrustedCertificate` through to `AppWindow.showCertificateTrustDialog`), then an attacker who can present the same self-signed/untrusted certificate from a second, different host could have that host's connection silently trusted after the user approved trust for a first host — since Electron's `dialog.showCertificateTrustDialog` trusts by certificate, not by hostname. This could enable credential/token exfiltration via a MITM'd API or git HTTPS endpoint that the user never explicitly authorized for that specific host.

### Likelihood Explanation
Requires the user to have already clicked through one untrusted-certificate warning (a real friction point, and one Desktop explicitly warns about via the dialog's destructive-styled "Add certificate" button). Full exploitability also depends on unverified main-process correlation logic in `main.ts`, which I could not confirm within this review.

### Recommendation
Thread the host (or full URL) through `onContinue` into `onContinueWithUntrustedCertificate` in `app.tsx`, and extend `AppWindow.showCertificateTrustDialog` to accept and enforce a host parameter, ensuring any future certificate-error correlation/trust caching in `main.ts` is keyed on `(host, certificate)` rather than certificate alone.

### Proof of Concept
Static code inspection confirms the exact signatures cited above: `IUntrustedCertificateProps.onContinue: (certificate: Electron.Certificate) => void` and `AppWindow.showCertificateTrustDialog(certificate, message)` — neither takes a host/URL argument, despite `url`/`host` being available in-scope at the call site in `untrusted-certificate.tsx`. [6](#0-5)

### Citations

**File:** app/src/ui/untrusted-certificate/untrusted-certificate.tsx (L1-21)
```typescript
import * as React from 'react'
import * as URL from 'url'
import { Dialog, DialogContent, DialogFooter } from '../dialog'
import { OkCancelButtonGroup } from '../dialog/ok-cancel-button-group'

interface IUntrustedCertificateProps {
  /** The untrusted certificate. */
  readonly certificate: Electron.Certificate

  /** The URL which was being accessed. */
  readonly url: string

  /** The function to call when the user chooses to dismiss the dialog. */
  readonly onDismissed: () => void

  /**
   * The function to call when the user chooses to continue in the process of
   * trusting the certificate.
   */
  readonly onContinue: (certificate: Electron.Certificate) => void
}
```

**File:** app/src/ui/untrusted-certificate/untrusted-certificate.tsx (L36-48)
```typescript
    const host = URL.parse(this.props.url).hostname

    return (
      <Dialog
        title={__DARWIN__ ? 'Untrusted Server' : 'Untrusted server'}
        onDismissed={this.props.onDismissed}
        onSubmit={this.onContinue}
        type={__DARWIN__ ? 'warning' : 'error'}
      >
        <DialogContent>
          <p>
            GitHub Desktop cannot verify the identity of {host}. The certificate
            ({this.props.certificate.subjectName}) is invalid or untrusted.{' '}
```

**File:** app/src/ui/untrusted-certificate/untrusted-certificate.tsx (L76-79)
```typescript
  private onContinue = () => {
    this.props.onDismissed()
    this.props.onContinue(this.props.certificate)
  }
```

**File:** app/src/main-process/app-window.ts (L353-366)
```typescript
  /** Send a certificate error to the renderer. */
  public sendCertificateError(
    certificate: Electron.Certificate,
    error: string,
    url: string
  ) {
    ipcWebContents.send(
      this.window.webContents,
      'certificate-error',
      certificate,
      error,
      url
    )
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
