### Title
`ThresholdKeys`/`ThresholdView` `Debug` implementations leak secret key material (scalar, offset, and interpolation coefficients) into log output - (File: crypto/dkg/src/lib.rs)

### Summary
The Maven advisory (CWE-532) concerned a plugin that failed to mask sensitive build variables in logs. The Serai analog: `ThresholdKeys` derives `fmt::Debug`, and `ThresholdCore`/`ThresholdView` implement `Debug` such that secret scalars — the key `scalar`, `offset`, and the `Interpolation::Constant` coefficient vector, which consists of the actual secret share scalars — are formatted into any `{:?}`/`{keys:?}` log, error, or trace message. Other secret fields (`secret_share`, `coefficients`, `enc_key`) were deliberately excluded via `finish_non_exhaustive()`, showing the intent to mask secrets; these fields were missed.

### Finding Description
`ThresholdKeys` uses `#[derive(Clone, Debug, Zeroize)]` at `crypto/dkg/src/lib.rs:291-301`. The derived `Debug` prints every field: `core` (whose manual `Debug` at lines 266-276 prints `interpolation`), plus the plaintext `scalar: C::F` and `offset: C::F`.

`ThresholdView`'s manual `Debug` (lines 316-328) explicitly prints `interpolation`, `scalar`, and `offset`:

```rust
// crypto/dkg/src/lib.rs:316-328
.field("interpolation", &self.interpolation)
.field("scalar", &self.scalar)
.field("offset", &self.offset)
```

`Interpolation` itself derives `Debug` (line 211), so `Interpolation::Constant(Vec<F>)` prints the full coefficient vector. Under Constant interpolation, `interpolation_factor` returns `c[i - 1]` (line 228) — i.e., each coefficient is the participant's secret share scalar used directly in the signing linear combination (`secret_share * interpolation_factor`, lines 494-498). These coefficients are secret key material.

Contrast with the deliberate masking elsewhere: `ThresholdCore::zeroize` wipes `secret_share` and `interpolation` (lines 282-286), `SecretShare` uses `finish_non_exhaustive` (pedpop `lib.rs:242-245`), `SecretShareMachine` omits `coefficients` (pedpop `lib.rs:285-294`), and `Encryption` omits `enc_key` (`encryption.rs:410-419`). `scalar`/`offset` are documented as secret-equivalent tweaks ("to allow for various account and privacy schemes", lines 393-411) — recovering `scalar`/`offset` plus the public tweaked `group_key` relationship leaks the private composition of the tweak.

### Impact Explanation
Any `log::{debug,trace}!`, `format!("{:?}", keys)`, panic message, or error-reporting path that formats a `ThresholdKeys` or `ThresholdView` emits the participant's secret interpolation coefficients and/or secret tweak scalars into log files or telemetry. For `Interpolation::Constant` keys, the emitted coefficients are the secret share scalars themselves; combined with the public `group_key`, an attacker reading logs can recover the effective secret share and forge signatures. This is exactly the CWE-532 class: sensitive secret values written to diagnostic output readable by parties without key access.

### Likelihood Explanation
`Debug` is implemented precisely so these types can be logged. Serai's processor code logs protocol state liberally (`info!("Generating new key. ID: {id:?} Params: {params:?} ...")`, `processor/src/key_gen.rs:312`), and Rust ecosystems routinely `{:?}`-format key/state structs in errors, `unwrap`/`expect` paths (`DkgError` formatting includes params), and tracing spans. The leak requires only a local/lower-privileged reader of logs — a lower bar than the cryptographic attacks and matching the Medium severity of the source advisory. It is not dependent on a malicious participant; any accessor of logs (ops tooling, crash reporters) receives key material.

### Recommendation
Remove `Debug` from the derive on `ThresholdKeys` and implement it manually with `finish_non_exhaustive()`, excluding `scalar`, `offset`, and `core`. In `ThresholdView::fmt` and `ThresholdCore::fmt`, drop the `interpolation`, `scalar`, and `offset` fields (retain `params`, `group_key`, `included`, `verification_shares`). Alternatively, redact `Interpolation::Constant`'s `Vec<F>` by implementing `Debug` manually for `Interpolation` that prints only the variant name. Add a regression test asserting `format!("{keys:?}")` does not contain the hex of any secret scalar.

### Proof of Concept
```rust
// Anywhere ThresholdKeys is formatted, e.g. a log or error path:
let keys: ThresholdKeys<C> = key_gen_machine.complete(shares).unwrap();
let log_line = format!("{keys:?}");
// log_line contains `scalar: <F>`, `offset: <F>`, and
// `interpolation: Constant([<F>, <F>, ...])` — the secret share scalars.
// For a view:
let view = keys.view(included).unwrap();
let leak = format!("{view:?}"); // prints scalar, offset, Constant coefficients
```
Root cause confirmed at `crypto/dkg/src/lib.rs:291` (derived Debug), `crypto/dkg/src/lib.rs:316-328` (explicit secret fields), and `crypto/dkg/src/lib.rs:211` (`Interpolation` derived Debug over `Vec<F>` of secret coefficients).