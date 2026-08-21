# Q2608: set_locale: A `locale` param like `../../etc` or a symbol-cast payload prob...

## Question
Can an unprivileged attacker (the `locale` param and `session[:locale]`) reach `set_locale` in lib/shopify_app/controller_concerns/localization.rb via any localized controller action with a `locale` param, supplying a `locale` param like `../../etc` or a symbol-cast payload probing `I18n.with_locale` / available_locales handling, so that locale selection must be constrained to available locales and never influence file/template resolution is violated, leading to unexpected template/render behavior from attacker locale (only if it yields real impact)? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/controller_concerns/localization.rb — `set_locale`
- Entrypoint: any localized controller action with a `locale` param
- Attacker controls: the `locale` param and `session[:locale]` — specifically a `locale` param like `../../etc` or a symbol-cast payload probing `I18n.with_locale` / available_locales handling.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: locale selection must be constrained to available locales and never influence file/template resolution
- Expected Immunefi impact: unexpected template/render behavior from attacker locale (only if it yields real impact) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
