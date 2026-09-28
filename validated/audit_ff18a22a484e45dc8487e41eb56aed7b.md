### Title
Secret key material exposed via `Debug`/`Display` formatting of threshold key types (log insertion of sensitive data) - (File: crypto/dkg/src/lib.rs)

### Summary
The IoTDB CVE class — sensitive information inserted into log files — has a direct analog in Serai's DKG crate. `ThresholdKeys` derives `Debug`, and the manual `Debug` impls on `ThresholdCore`/`ThresholdView` print the `interpolation` field, which for `Interpolation::Constant(Vec<F>)` contains secret scalars. Any `{:?}`/`{keys}`-style logging of these types writes secret share material or ephemeral scalar tweaks into logs.

### Finding Description
Three leak paths exist in `crypto/dkg/src/lib.rs`:

1. `ThresholdKeys<C>` uses `#[derive(Clone, Debug, Zeroize)]` (line 291). Derived `Debug` prints every field, including `scalar: C::F` and `offset: C::F` (lines 298–300). These are caller-supplied secret tweaks applied via `scale()`/`offset()` (used by account/privacy schemes and key recovery flows). The doc comments explicitly say these are "ephemeral and will not be included when these keys are serialized" — yet `Debug` serializes them in plaintext into any log or error message that formats the keys.

2. `ThresholdCore<C>`'s manual `Debug` (lines 266–276) correctly hides `secret_share` via `finish_non_exhaustive()`, but prints `interpolation`. `Interpolation::Constant(Vec<F>)` (line 219) stores a vector of field elements — one coefficient per secret key share. For constant interpolation (`t == n`, used by MuSig-style aggregation and recovered/promoted keys), `interpolation_factor` returns `c[i-1]` directly (line 228), i.e., these coefficients *are* secret-share-equivalent scalars. `Interpolation` derives `Debug` (line 211), so `{:?}` on `ThresholdCore`/`ThresholdKeys` dumps all coefficients.

3. `ThresholdView<C>`'s manual `Debug` (lines 316–329) hides `secret_share` but again prints `interpolation`, `scalar`, and `offset` — leaking the same secret coefficients plus the interpolated tweak applied to `included[0]` (lines 518–521).

By contrast, `pedpop` deliberately redacts secrets: `SecretShare`'s `Debug` prints only `finish_non_exhaustive()` and `Encryption`'s `Debug` omits `enc_key` (encryption.rs:410–419). The DKG types lack this discipline.

### Impact Explanation
The confidential data written to logs is exactly what the codebase treats as private key-equivalent: `ThresholdCore::secret_share`'s analogs (constant-interpolation coefficients), and the secret `scalar`/`offset` tweaks. Whoever obtains the log output (log aggregation, crash reports, `{:?}` in error propagation via `io::Error::other(format!("{e:?}"))`, RPC error strings) recovers scalar material that can be combined with public verification shares to reconstruct or attack the threshold key. This mirrors the CVE's C:H impact: exposure of sensitive information to an unauthorized actor.

### Likelihood Explanation
`Debug` on these types is reachable in normal production patterns: any `log::debug!("{:?}", keys)`, `format!("{keys:?}")` in an error, or debug-assertion path in the coordinator/processor that wraps a `ThresholdKeys`/`ThresholdView` emits the secrets. No attacker action is needed beyond log access; no secret input from an unprivileged party is required — the mere use of constant-interpolation keys or offset tweaks plus routine debug logging leaks them.

### Recommendation
- Remove `Debug` from `ThresholdKeys`'s derive list and implement a manual `Debug` that prints only `core` (whose own impl is partially safe) plus `finish_non_exhaustive()` — never `scalar`/`offset`.
- Add a manual `Debug` for `Interpolation` that prints only the variant tag (`"Constant"` / `"Lagrange"`), not the coefficient vector.
- Remove `scalar`/`offset` fields from `ThresholdView`'s `Debug`.

### Proof of Concept
```rust
// crypto/dkg context: t == n key with constant interpolation
let coefficients = vec![secret_share_1, secret_share_2]; // secret scalars
let keys = ThresholdKeys::<Ed25519>::new(
    ThresholdParams::new(2, 2, Participant::new(1).unwrap()).unwrap(),
    Interpolation::Constant(coefficients.clone()),
    Zeroizing::new(secret_share_1),
    verification_shares,
).unwrap();

// Any of these leaks secrets to logs/errors:
format!("{keys:?}");          // prints scalar + offset fields (derived Debug)
format!("{:?}", keys.view(vec![p1, p2]).unwrap()); // prints Interpolation::Constant([...secrets...])
// The Debug output contains the raw secret coefficients c[i] in plaintext.
```