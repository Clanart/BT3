# Q2609: set_locale: A `locale` object that is not a string so `.to_sym`/`.split` mi...

## Question
Can an unprivileged attacker (the `locale` param and `session[:locale]`) reach `set_locale` in lib/shopify_app/controller_concerns/localization.rb via any localized controller action with a `locale` param, supplying a `locale` object that is not a string so `.to_sym`/`.split` misbehaves, so that locale selection must be constrained to available locales and never influence file/template resolution is violated, leading to unexpected template/render behavior from attacker locale (only if it yields real impact)? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: lib/shopify_app/controller_concerns/localization.rb — `set_locale`
- Entrypoint: any localized controller action with a `locale` param
- Attacker controls: the `locale` param and `session[:locale]` — specifically a `locale` object that is not a string so `.to_sym`/`.split` misbehaves.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: locale selection must be constrained to available locales and never influence file/template resolution
- Expected Immunefi impact: unexpected template/render behavior from attacker locale (only if it yields real impact) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
