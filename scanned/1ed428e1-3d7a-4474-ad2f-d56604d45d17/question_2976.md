# Q2976: ISignInState: deep-link / OAuth callback consent bypass or account binding

## Question
Can `ISignInState` in [app/src/lib/stores/sign-in-store.ts] be reached repeatedly or with crafted URL fields to bind, rebind, or silently switch the active account/endpoint from unprivileged deep-link input?

## Target
- File/function: [app/src/lib/stores/sign-in-store.ts] — `ISignInState`
- Entrypoint: An `x-github-client://` / `github-mac://` / `github-windows://` deep link or its OAuth callback that a page the user merely visits can trigger
- Attacker controls: deep-link URL and its parameters, OAuth `state`/`code`, target endpoint or account in the link
- Exploit idea: Can `ISignInState` in [app/src/lib/stores/sign-in-store.ts] be reached repeatedly or with crafted URL fields to bind, rebind, or silently switch the active account/endpoint from unprivileged deep-link input?
- Invariant to test: no deep link finishes authentication, changes the active account/endpoint, or triggers a repo action without a fresh explicit user consent step and validated OAuth state
- Expected Immunefi impact: Critical - attacker completes sign-in, binds an attacker account or endpoint, or drives a repository action without fresh explicit consent (target scope: "Critical. A `x-github-client://` / `github-mac://` / `github-windows://` deep link or its OAuth callback lets a page the...")
- Fast validation: Dispatch a crafted deep-link URL to `ISignInState` in a test and assert it validates state/consent and refuses to auto-complete auth or bind an endpoint
