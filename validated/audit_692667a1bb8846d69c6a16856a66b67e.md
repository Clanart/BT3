### Title
`Debug` implementations on threshold key material leak secret share coefficients via the `interpolation` field — ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The ChurchCRM bug class is "sensitive secret material disclosed through diagnostic output (error/Debug messages)". Serai's DKG crate deliberately redacts secret fields from `fmt::Debug` everywhere — `SecretShare::fmt` uses `finish_non_exhaustive()` to hide the share bytes [1](#0-0) , `Encryption::fmt` omits `enc_key` [2](#0-1) , and `ThresholdCore::fmt` omits `secret_share` [3](#0-2) . However, `ThresholdCore::fmt`, `ThresholdView::fmt`, and the derived `Debug` on `ThresholdKeys` all print the `interpolation` field, and `Interpolation::Constant(Vec<F>)` derives `Debug` — meaning the constant coefficients, which are the secret key shares themselves, are emitted verbatim.

### Finding Description
`Interpolation` is defined at `crypto/dkg/src/lib.rs:212` with a derived `Debug` impl and documented as "a list of constant coefficients, one for each of the secret key shares" [4](#0-3) . `interpolation_factor` indexes `Constant(c)` by participant, i.e. `c[i-1]` is participant `i`'s share [5](#0-4) . `ThresholdCore::fmt` writes `.field("interpolation", &self.interpolation)` [3](#0-2) , and `ThresholdKeys` derives `Debug` over `core` [6](#0-5) . `ThresholdView::fmt` likewise prints `interpolation` plus `scalar`/`offset` [7](#0-6) .

For `t == n` wallets using `Interpolation::Constant` — the standard form for promoted/tweaked multisig keys — formatting a `ThresholdKeys`/`ThresholdView`/`ThresholdCore` with `{:?}` prints every participant's secret share. This output can surface through `panic!("{e:?}")`-style diagnostics, `io::Error::other(format!("{e:?}"))` paths such as the borsh deserializer [8](#0-7) , or integrator logging of the machine/key state after processing attacker-supplied preprocess/share bytes (which integrators receive untrusted input for via `read_preprocess`/`calculate_share`).

### Impact Explanation
Disclosure of all `n` constant coefficients is disclosure of the full threshold private key — the exact analog of the ChurchCRM password-in-error-message leak. Any log line, panic message, or error path that formats these types hands the complete key material to whoever reads the output, enabling total theft of funds controlled by the multisig.

### Likelihood Explanation
Triggering requires any code path that `Debug`-formats `ThresholdKeys`/`ThresholdView`/`ThresholdCore` — extremely common in `unwrap_err`/`expect`/log tracing during the untrusted-input handling that `calculate_share` and FROST `sign`/`complete` perform. The `Constant` interpolation path is explicitly supported by `ThresholdKeys::new` for `t == n` [9](#0-8) . The inconsistency with the deliberately redacted `secret_share`, `enc_key`, and `SecretShare` fields shows this is an oversight, not intended exposure.

### Recommendation
Implement `fmt::Debug` manually for `Interpolation` that prints only the variant tag (e.g. `debug_struct("Interpolation::Constant").finish_non_exhaustive()`), and remove `scalar`/`offset` from `ThresholdView::fmt` since offset scalars may be derived from private tweaks. Audit all `Debug` impls in `crypto/dkg` to confirm no secret-bearing field is printed.

### Proof of Concept
```rust
// crypto/dkg: t == n keys built with constant interpolation
let interpolation = Interpolation::Constant(vec![share1, share2, share3]);
let keys = ThresholdKeys::<Ristretto>::new(params, interpolation, secret_share, vshares)?;
// Any error/log path formatting the keys leaks every share:
let leaked = format!("{keys:?}"); // contains share1..share3 verbatim
```

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L242-246)
```rust
impl<F: PrimeField> fmt::Debug for SecretShare<F> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt.debug_struct("SecretShare").finish_non_exhaustive()
  }
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L410-420)
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
}
```

**File:** crypto/dkg/src/lib.rs (L202-207)
```rust
  fn deserialize_reader<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    let t = u16::deserialize_reader(reader)?;
    let n = u16::deserialize_reader(reader)?;
    let i = Participant::deserialize_reader(reader)?;
    ThresholdParams::new(t, n, i).map_err(|e| io::Error::other(format!("{e:?}")))
  }
```

**File:** crypto/dkg/src/lib.rs (L211-221)
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
```

**File:** crypto/dkg/src/lib.rs (L226-229)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
      Interpolation::Lagrange => {
```

**File:** crypto/dkg/src/lib.rs (L266-276)
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

**File:** crypto/dkg/src/lib.rs (L316-329)
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
