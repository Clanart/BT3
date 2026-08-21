# Q2891: set_locale: A persisted hostile `session[:locale]` reused across requests

## Question
Can an unprivileged attacker (the `locale` param and `session[:locale]`) reach `set_locale` in lib/shopify_app/controller_concerns/localization.rb via any localized controller action with a `locale` param, supplying a persisted hostile `session[:locale]` reused across requests, so that locale selection must be constrained to available locales and never influence file/template resolution is violated, leading to unexpected template/render behavior from attacker locale (only if it yields real impact)? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: lib/shopify_app/controller_concerns/localization.rb — `set_locale`
- Entrypoint: any localized controller action with a `locale` param
- Attacker controls: the `locale` param and `session[:locale]` — specifically a persisted hostile `session[:locale]` reused across requests.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: locale selection must be constrained to available locales and never influence file/template resolution
- Expected Immunefi impact: unexpected template/render behavior from attacker locale (only if it yields real impact) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
