### Title
`Debug` impls on `ThresholdKeys`/`ThresholdView`/`Interpolation` leak secret shares, tweak scalars and offsets to logs - (File: crypto/dkg/src/lib.rs)

### Summary
The matrix-sdk-crypto advisory (GHSA-9ggc-845v-gcgv) concerns a logic bug causing private key material to be written to debug logs. Serai's `dkg` crate has the same bug class, built directly into its formatting impls: derived/manual `fmt::Debug` implementations on the threshold-key structures emit secret field elements — the per-participant secret-share coefficients of `Interpolation::Constant`, and the ephemeral `scalar`/`offset` tweaks — to anything that formats these types with `{:?}` (tracing `?` sigil, `format!("{:?}")`, `unwrap_err`/`expect` paths, panic dumps).

### Finding Description
`Interpolation` derives `Debug`, and its `Constant(Vec<F>)` variant is documented as "a list of constant coefficients, one for each of the secret key shares" — i.e., these field elements are private key material for every participant, not just the local one (crypto/dkg/src/lib.rs:211-221). `ThresholdCore` has a hand-written `Debug` impl that prints `params`, `group_key`, `verification_shares`, and `interpolation` — including the secret `Constant` coefficients — via `finish_non_exhaustive`, which hides only `secret_share` (crypto/dkg/src/lib.rs:266-276). `ThresholdKeys` then `#[derive(Debug)]` over `core`, `scalar`, and `offset` (crypto/dkg/src/lib.rs:290-301). `scalar` and `offset` are secret linear-combination factors applied to the real secret share: `view()` computes `secret_share = interpolation_factor * scalar * secret_share` and adds `offset` (crypto/dkg/src/lib.rs:493-521), so logging `scalar`/`offset` leaks the private tweak that separates an address's public key from the raw group key. `ThresholdView` repeats the pattern: its manual `Debug` prints `interpolation`, `scalar`, and `offset` while omitting `secret_share` (crypto/dkg/src/lib.rs:316-329).

So a single `{:?}` of `ThresholdKeys`/`ThresholdView` discloses: (a) for `Constant` interpolation (supported whenever `t == n`, per `ThresholdKeys::new` at crypto/dkg/src/lib.rs:367-374), every participant's secret share — the full private key; (b) for `Lagrange`, the secret `scalar`/`offset` tweaks, which combined with a verification share reduce the work to recovering one share instead of the threshold.

### Impact Explanation
Analogous to the advisory, a local party (or anyone who obtains debug/log output) recovers private key material: the complete set of secret shares under `Constant` interpolation, or the secret tweak values otherwise. This meets the "secret leakage to logs" impact class at Medium severity (local access required, high confidentiality loss).

### Likelihood Explanation
`ThresholdKeys` and `ThresholdView` are the primary public types handed to integrators and used throughout FROST signing (`view()` is called per signing set). Formatting them with `{:?}`/`{keys:?}` in `tracing`/`log` calls, error contexts (`io::Error::other(format!("{e:?}"))`-style wrappers already exist in this file, e.g. line 206), or assertion failures is a normal pattern, and the offending impls are silent — there is no marker preventing accidental formatting. The upstream CVE rated an identical "sometimes logs the private part" condition Medium.

### Recommendation
Remove `Debug` from `Interpolation`'s derive (or write a manual impl that prints only the variant tag and length). In `ThresholdCore::fmt` and `ThresholdView::fmt`, drop the `interpolation`, `scalar`, and `offset` fields (or render them as `"[redacted]"`), keeping `params`, `group_key`, `verification_shares`, and `included`, which are public. Add a regression test asserting `format!("{keys:?}")` output does not contain the serialized secret values.

### Proof of Concept
```rust
// crypto/dkg — conceptual PoC against in-scope code
use dkg::{ThresholdParams, ThresholdKeys, Interpolation, Participant};
use ciphersuite::Ciphersuite;
use zeroize::Zeroizing;
use std::collections::HashMap;

// t == n == 2, so Constant interpolation is permitted by ThresholdKeys::new
let params = ThresholdParams::new(2, 2, Participant::new(1).unwrap()).unwrap();

// Constant coefficients are the secret key shares themselves (lib.rs:213-219)
let coefficients = vec![secret_f_1, secret_f_2]; // Vec<C::F> — private material
let keys = ThresholdKeys::new(
  params,
  Interpolation::Constant(coefficients),
  Zeroizing::new(secret_f_1),
  verification_shares, // HashMap<Participant, C::G>
).unwrap();

// ThresholdKeys derives Debug -> ThresholdCore::fmt prints `interpolation`
// -> Interpolation::Constant's derived Debug prints every secret coefficient.
let leaked = format!("{keys:?}");
assert!(leaked.contains(&format!("{secret_f_1:?}"))); // private key material in logs

// Independently, the secret scalar/offset tweaks are printed by both
// ThresholdKeys (derive) and ThresholdView (manual impl, lib.rs:316-329):
let tweaked = keys.offset(secret_offset);
assert!(format!("{tweaked:?}").contains(&format!("{secret_offset:?}")));
```

The exposure is unconditional once the types are `{:?}`-formatted — matching the advisory's mechanics (secret field elements reaching debug logs), with the Serai variant being worse under `Constant` interpolation since it emits all participants' shares rather than one private key.