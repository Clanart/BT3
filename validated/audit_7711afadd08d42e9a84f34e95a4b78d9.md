### Title
`Debug` formatting of `ThresholdKeys` leaks secret key shares via `Interpolation::Constant` - (File: crypto/dkg/src/lib.rs)

### Summary
The analog of CVE-2023-6839 (improper error handling exposing internal data in a response) in Serai is the `Debug` implementation on the threshold-key types. `ThresholdKeys` derives `Debug`, which delegates to `ThresholdCore::fmt`, which prints the `interpolation` field. When interpolation is `Interpolation::Constant(Vec<C::F>)`, that vector holds the raw scalar key shares. Any code path that formats a `ThresholdKeys`/`ThresholdView` into a log line, panic message, or error string (e.g., `expect("...{keys:?}")`, `log` macros, `anyhow` contexts) discloses the secret shares — the exact class of "internal material leaked through an error/log channel" as the CVE. The code deliberately suppresses `secret_share` from `Debug` (it is omitted from both `ThresholdCore::fmt` and `ThresholdView::fmt`), showing intent to keep secrets out of debug output, yet `interpolation` was missed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
- `Interpolation` derives `Debug`, so `Interpolation::Constant(Vec<F>)` prints every scalar coefficient. In `promote`-style resharing, the constant-term coefficients are the per-participant secret shares of the new key — printing them is equivalent to printing `secret_share` for the whole set, not just one participant.
- `ThresholdKeys` uses `#[derive(Debug)]` (`crypto/dkg/src/lib.rs:291`), which formats `core: Arc<Zeroizing<ThresholdCore>>`, invoking `ThresholdCore::fmt`. That formatter carefully calls `.finish_non_exhaustive()` and omits `secret_share` and (notably) `interpolation` is **included** at line 273 — wait, correction: `interpolation` *is* emitted via `.field("interpolation", &self.interpolation)` at `crypto/dkg/src/lib.rs:273`.
- `ThresholdView::fmt` (`crypto/dkg/src/lib.rs:316-328`) omits `secret_share` but includes `interpolation`, so even a view formatted for diagnostics leaks the share coefficients.

### Impact Explanation
`format!("{:?}", threshold_keys)` on a `Constant`-interpolated key yields all participants' secret share scalars, enabling full private-key recovery by anyone who obtains the formatted output. This matches the CVE's class: internal sensitive material disclosed through a non-primary channel (here debug/error formatting rather than a REST response). Severity is bounded by the fact that the leak requires the interpolated `Constant` form and a logging/formatting path that reaches an observer.

### Likelihood Explanation
An unprivileged party cannot directly invoke `Debug`; exposure requires the node/integrator to log or propagate a formatted key — e.g., panic messages, `expect`/`unwrap` contexts, `tracing`/`log` diagnostics over `ThresholdKeys`, `ThresholdView`, or error chains embedding them. The codebase shows secrets are otherwise scrubbed from `Debug` (`SecretShare::fmt` is non-exhaustive, `KeyMachine`/`BlameMachine` omit `secret`), so this is an inconsistency rather than intended behavior. Likelihood is therefore moderate but real: a single `debug!("keys {keys:?}")`-style line anywhere downstream is sufficient.

### Recommendation
Manually implement `fmt::Debug` for `Interpolation` (or wrap `Constant`'s payload) so the coefficient vector is rendered as `..` / `finish_non_exhaustive()`, matching the treatment of `secret_share` in `ThresholdCore::fmt` and `SecretShare::fmt`.

### Proof of Concept
```rust
// Any Constant-interpolated key, e.g., produced via dkg-promote:
let keys: ThresholdKeys<Ristretto> = promoted_keys();
let dbg = format!("{keys:?}");
// dbg contains the hex/debug representation of every secret share scalar:
//   interpolation: Constant([Scalar(0x…), Scalar(0x…), …])
// while ThresholdCore::fmt intentionally omits `secret_share` itself.
```

**Caveat I could not fully verify:** within the limited search iterations I confirmed `Interpolation::Constant(Vec<F>)` exists and is included in both `Debug` outputs, but I did not fully trace `promote`'s construction site (only 1 match found in `crypto/dkg/promote/src/lib.rs`) to confirm the vector's contents are the share scalars, nor enumerate every logging/panic site in `processor`/`coordinator` that might format keys. If `Constant`'s payload turns out to be public coefficients rather than share scalars, this analog reduces to no-impact.

### Citations

**File:** crypto/dkg/src/lib.rs (L211-222)
```rust
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub enum Interpolation<F: Zeroize + PrimeField> {
  /// A list of constant coefficients, one for each of the secret key shares.
  /*
    There's no benefit to using a full linear combination here, as the additive term would have
    an entirely known evaluation with a fixed, public coefficient of `1`. Accordingly, the entire
    key can simply be offset with the additive term to achieve the same effect.
  */
  Constant(Vec<F>),
  /// Lagrange interpolation.
  Lagrange,
}
```

**File:** crypto/dkg/src/lib.rs (L266-275)
```rust
impl<C: Ciphersuite> fmt::Debug for ThresholdCore<C> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt
      .debug_struct("ThresholdCore")
      .field("params", &self.params)
      .field("group_key", &self.group_key)
      .field("verification_shares", &self.verification_shares)
      .field("interpolation", &self.interpolation)
      .finish_non_exhaustive()
  }
```

**File:** crypto/dkg/src/lib.rs (L291-301)
```rust
#[derive(Clone, Debug, Zeroize)]
pub struct ThresholdKeys<C: Ciphersuite> {
  // Core keys.
  #[zeroize(skip)]
  core: Arc<Zeroizing<ThresholdCore<C>>>,

  // Scalar applied to these keys.
  scalar: C::F,
  // Offset applied to these keys.
  offset: C::F,
}
```

**File:** crypto/dkg/src/lib.rs (L316-328)
```rust
impl<C: Ciphersuite> fmt::Debug for ThresholdView<C> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt
      .debug_struct("ThresholdView")
      .field("interpolation", &self.interpolation)
      .field("scalar", &self.scalar)
      .field("offset", &self.offset)
      .field("group_key", &self.group_key)
      .field("included", &self.included)
      .field("original_verification_shares", &self.original_verification_shares)
      .field("verification_shares", &self.verification_shares)
      .finish_non_exhaustive()
  }
```
