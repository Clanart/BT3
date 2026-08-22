# Q0600: parseAppURL: deep-link / OAuth callback consent bypass or account binding

## Question
Does `parseAppURL` in [app/src/lib/parse-app-url.ts] accept an OAuth callback without strictly validating the `state` parameter, letting an attacker inject their own `code` and associate the victim session with an attacker-controlled authorization?

## Target
- File/function: [app/src/lib/parse-app-url.ts] — `parseAppURL`
- Entrypoint: An `x-github-client://` / `github-mac://` / `github-windows://` deep link or its OAuth callback that a page the user merely visits can trigger
- Attacker controls: deep-link URL and its parameters, OAuth `state`/`code`, target endpoint or account in the link
- Exploit idea: Does `parseAppURL` in [app/src/lib/parse-app-url.ts] accept an OAuth callback without strictly validating the `state` parameter, letting an attacker inject their own `code` and associate the victim session with an attacker-controlled authorization?
- Invariant to test: no deep link finishes authentication, changes the active account/endpoint, or triggers a repo action without a fresh explicit user consent step and validated OAuth state
- Expected Immunefi impact: Critical - attacker completes sign-in, binds an attacker account or endpoint, or drives a repository action without fresh explicit consent (target scope: "Critical. A `x-github-client://` / `github-mac://` / `github-windows://` deep link or its OAuth callback lets a page the...")
- Fast validation: Dispatch a crafted deep-link URL to `parseAppURL` in a test and assert it validates state/consent and refuses to auto-complete auth or bind an endpoint
