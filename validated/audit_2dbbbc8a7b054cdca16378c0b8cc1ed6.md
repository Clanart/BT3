### Title
`ThresholdCore` / `ThresholdView` `Debug` impls leak secret share coefficients via `Interpolation::Constant` — (File: crypto/dkg/src/lib.rs)

### Summary
The CWE-532 bug class (secret material emitted into routine output/logs) maps directly onto Serai's `Debug` implementations in `crypto/dkg`. `ThresholdCore::fmt` and `ThresholdView::fmt` print the `interpolation` field, and `Interpolation` derives `Debug`, so for `Interpolation::Constant(Vec<F>)` the full vector of secret coefficients — which are the participants' key shares — is rendered into any `{?}` formatting of `ThresholdKeys`/`ThresholdView`.

### Finding Description
`Interpolation<F>` is defined with `#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]` and its `Constant` variant holds `Vec<F>`: "A list of constant coefficients, one for each of the secret key shares." These are secret scalars — `interpolation_factor` indexes them as `c[i - 1]` and multiplies them into `secret_share` when producing a signing view. Meanwhile, the hand-written `Debug` impls deliberately exclude `secret_share` via `finish_non_exhaustive()` but still emit `.field("interpolation", &self.interpolation)`:

- `ThresholdCore::fmt` prints `interpolation` (crypto/dkg/src/lib.rs:266–275), reached through `ThresholdKeys`' `#[derive(Debug)]` (line 291), which formats `core: Arc<Zeroizing<ThresholdCore<C>>>`.
- `ThresholdView::fmt` prints `interpolation` (lines 316–328), plus `scalar` and `offset`.

So `format!("{keys:?}")`, a `log::debug!`/`tracing` span, or an error wrapper (`io::Error::other(format!("{e:?}"))`-style propagation) on any structure embedding `ThresholdKeys`/`ThresholdView` serializes every constant coefficient. The `Interpolation::Constant` form is produced by the `promote`/`recovery` DKG paths (crypto/dkg/promote), i.e. normal production operation, not an exotic state.

This is exactly the advisory's shape: `setup-steamcmd` post-job printed `config.vdf` containing the auth token; here the "post-processing" formatting path prints the secret coefficients. The careful `Zeroizing`/`finish_non_exhaustive` hygiene on `secret_share` shows the intent to keep this material out of output — `interpolation` slips through.

### Impact Explanation
With `Interpolation::Constant`, the printed coefficients are the complete set of participant secret shares (`c[i]` is the share for participant `i+1`). Any party — or any log aggregation, CI output, error report, or crash handler that receives a `Debug`-formatted `ThresholdKeys`/`ThresholdView` — recovers all secret key shares and therefore the full threshold private key. For a Bitcoin-network key this means unilateral spend of all multisig funds; the token-theft impact of the original advisory maps to total key compromise here. In a threshold setting, a single faulty/malicious participant who can induce or merely observe such formatted output (e.g., via the `AdditionalBlameMachine`/`PedPoPError` paths that embed debuggable state) obtains shares belonging to honest participants without breaking any cryptographic assumption.

### Likelihood Explanation
`ThresholdKeys` is `pub` and `Debug` is required upstream (`SignData` in coordinator code, `Message` bounds in pedpop, `Addendum: Debug`, error enums via `thiserror`), so `{:?}`/`anyhow`/`tracing` capture is a normal occurrence rather than misuse. The leak requires the `Constant` interpolation variant specifically — used by promotion/recovery flows — plus any formatting of the keys/view; both are routine. It does not require the attacker to send malformed input, which keeps it in scope as a secret-exposure analog rather than a triggered-exploit path.

### Recommendation
- Remove `.field("interpolation", ...)` from `ThresholdCore::fmt` and `ThresholdView::fmt`, or print only a discriminant (`"Constant"`/`"Lagrange"`) without contents.
- Drop `Debug` from the `Interpolation` derive, or implement `Debug` manually to suppress the `Vec<F>` payload.
- Audit sibling structs for the same pattern (e.g., derived `Debug` on `ThresholdKeys` printing `scalar`/`offset`, and any error enums embedding key material).

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dkg::{Interpolation, ThresholdParams, Participant, ThresholdKeys};
use std::collections::HashMap;
use zeroize::Zeroizing;

// ThresholdKeys produced by a promote/recovery flow use Interpolation::Constant
let keys: ThresholdKeys<Ristretto> = recover_with_constant_interpolation();

// Any logging / error formatting leaks every participant's secret share scalar
let leaked = format!("{keys:?}");
// leaked contains "interpolation: Constant([<F>, <F>, ...])" —
// each <F> is a full secret key share, sufficient to reconstruct the private key.
```

The leak occurs because `ThresholdCore::fmt` emits `interpolation` while `Interpolation`'s derived `Debug` prints the raw `Vec<C::F>` secret coefficients (crypto/dkg/src/lib.rs:211–222, 266–275, 291, 316–328).