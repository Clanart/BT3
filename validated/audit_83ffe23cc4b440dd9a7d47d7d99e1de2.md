### Title
Secret key shares leaked via `Debug` formatting of `Interpolation::Constant` in `ThresholdCore` / `ThresholdView` - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The CVE-2019-9444 bug class is "sensitive internal values disclosed through formatted output" (kernel pointers printed with `%p`). The Serai analog: `Interpolation` derives `Debug`, and `Interpolation::Constant` wraps `Vec<F>` — the documentation states these coefficients are "one for each of the secret key shares". Both `ThresholdCore`'s and `ThresholdView`'s manual `Debug` impls include `.field("interpolation", ...)`, so any `{:?}`/`{:#?}` formatting, `unwrap_err`/panic message, or structured log of `ThresholdKeys`/`ThresholdView` prints every participant's raw secret share in plaintext.

### Finding Description
`Interpolation` is defined with `#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]`, so `Constant(Vec<F>)` renders its contents when formatted [1](#0-0) . The coefficients are explicitly the secret key shares ("A list of constant coefficients, one for each of the secret key shares") [2](#0-1) .

`ThresholdCore::fmt` emits `interpolation` while carefully omitting `secret_share` via `finish_non_exhaustive` [3](#0-2) . `ThresholdKeys` derives `Debug`, which delegates to `Arc<Zeroizing<ThresholdCore>>` → `ThresholdCore`'s `Debug` [4](#0-3) . `ThresholdView::fmt` likewise prints `interpolation` [5](#0-4) . Scalar types in `dalek-ff-group`/`kp256` implement `Debug` that renders the field element value, so the shares are output in full.

By contrast, other secret material is correctly redacted: `SecretShare`'s `Debug` prints only `finish_non_exhaustive()` [6](#0-5) , and `Encryption`'s `Debug` omits `enc_key` [7](#0-6)  — showing the intended invariant that secrets never appear in formatted output is violated here.

### Impact Explanation
Under `Interpolation::Constant` (required when `t == n` [8](#0-7) ), the coefficient vector is literally the per-participant secret shares for participants 1..=n. A single `Debug` print of `ThresholdKeys` or `ThresholdView` — e.g., a `log::debug!("{keys:?}")`, an error path embedding the struct, or an assertion failure — discloses all shares, allowing full reconstruction of the threshold private key (constant interpolation means each coefficient directly is a share; any `t` of them, or simply the sum structure, yields the group secret). This is a complete key-share-recovery impact, strictly stronger than the pointer-leak info disclosure of the reference CVE.

### Likelihood Explanation
Exploitation requires an attacker to observe formatted output containing the key material (log files, crash reports, error traces) — analogous to the CVE's "system execution privileges needed" precondition. `Constant` interpolation is the mandated mode for `t == n` setups, so affected configurations are realistic. While Serai's own code avoids printing keys, `Debug` is a public API surface: any integrator or downstream processor code (e.g., `KeyGen` which derives `Debug` over machines holding key material) can leak it with a single format call. Rated Medium: high impact when triggered, but requires access to local debug output rather than being reachable purely from untrusted wire bytes.

### Recommendation
Implement `fmt::Debug` manually for `Interpolation` (or wrap `Vec<F>` in a redacting newtype) emitting `finish_non_exhaustive()` without printing coefficients, matching the redaction pattern already used for `SecretShare`, `Encryption::enc_key`, and `ThresholdCore::secret_share`. This preserves `Debug` usability while eliminating the secret leak.

### Proof of Concept
```rust
// Requires t == n so Interpolation::Constant is permitted
let interpolation = Interpolation::Constant(vec![share_1, share_2, share_3]);
let keys = ThresholdKeys::<Ristretto>::new(params, interpolation, my_share, vshares)?;
// Leaks every participant's secret share:
let leaked = format!("{keys:?}"); // contains share_1, share_2, share_3 in plaintext
```
Tracing the path: `ThresholdKeys`'s derived `Debug` → `Arc<Zeroizing<ThresholdCore>>` `Debug` → `ThresholdCore::fmt` → `interpolation` field → derived `Debug` on `Interpolation::Constant(Vec<F>)` → each `F`'s `Debug` prints the scalar bytes.

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

**File:** crypto/dkg/src/lib.rs (L367-374)
```rust
    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L242-246)
```rust
impl<F: PrimeField> fmt::Debug for SecretShare<F> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt.debug_struct("SecretShare").finish_non_exhaustive()
  }
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L410-419)
```rust
impl<C: Ciphersuite> fmt::Debug for Encryption<C> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt
      .debug_struct("Encryption")
      .field("context", &self.context)
      .field("i", &self.i)
      .field("enc_pub_key", &self.enc_pub_key)
      .field("decryption", &self.decryption)
      .finish_non_exhaustive()
  }
```
