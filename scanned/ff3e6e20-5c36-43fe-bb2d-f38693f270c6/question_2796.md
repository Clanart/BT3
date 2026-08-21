# Q2796: set_locale: A `locale` object that is not a string so `.to_sym`/`.split` mi...

## Question
Can an unprivileged attacker (the `locale` param and `session[:locale]`) reach `set_locale` in lib/shopify_app/controller_concerns/localization.rb via any localized controller action with a `locale` param, supplying a `locale` object that is not a string so `.to_sym`/`.split` misbehaves, so that locale selection must be constrained to available locales and never influence file/template resolution is violated, leading to unexpected template/render behavior from attacker locale (only if it yields real impact)? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/controller_concerns/localization.rb — `set_locale`
- Entrypoint: any localized controller action with a `locale` param
- Attacker controls: the `locale` param and `session[:locale]` — specifically a `locale` object that is not a string so `.to_sym`/`.split` misbehaves.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: locale selection must be constrained to available locales and never influence file/template resolution
- Expected Immunefi impact: unexpected template/render behavior from attacker locale (only if it yields real impact) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
