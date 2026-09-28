### Title
`Debug` implementation on `ThresholdKeys` discloses the secret key share - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys<C>` derives `fmt::Debug`, which formats every field — including `core`, the `Arc<Zeroizing<ThresholdCore<C>>>` that stores `secret_share`, the participant's private threshold share. Any `{:?}`/`{keys:?}` formatting, log statement, `unwrap_err` on a `Result` containing keys, or debug/trace output emits the raw secret scalar to whatever lower-privileged consumer can read the log/debug channel. This is the same bug class as CVE-2017-7486: a non-secret accessor surface (a Postgres view there, a `Debug` impl here) disclosing stored secret material to parties who have access to the accessor but are not entitled to the secret.

### Finding Description
`ThresholdKeys` is defined with `#[derive(Clone, Debug, Zeroize)]` and holds its secret in `core`: [1](#0-0) 

The `#[zeroize(skip)]` attribute suppresses zeroization of `core`, but the `Debug` derive still formats it — `Arc<T>` forwards `Debug` to `T`, and `Zeroizing<T>` forwards `Debug` to `T` (the `zeroize` crate passes through `Debug`), so `ThresholdCore`'s `secret_share` field (`Zeroizing<C::F>`) is rendered in plaintext.

The codebase's own conventions confirm this is unintended exposure rather than accepted design:

- `ThresholdView` — a struct holding the *same kind* of `secret_share` — has a hand-written `Debug` impl that uses `finish_non_exhaustive()` specifically to omit it: [2](#0-1) 

- `SecretShare` in PedPoP has a manual `Debug` that prints only `debug_struct("SecretShare").finish_non_exhaustive()`, and a comment noting the type must "highlight the expected behavior" around secrecy: [3](#0-2) 

- `BlameMachine` likewise has a manual `Debug` that omits the `result` (which contains `ThresholdKeys`/the share) via `finish_non_exhaustive()`: [4](#0-3) 

So every neighboring type that can carry the share was deliberately given a redacting `Debug`; `ThresholdKeys` itself was not. Because `ThresholdKeys` is the long-lived object produced by `BlameMachine::complete()` and passed into `AlgorithmMachine::new`/`read`/`serialize` throughout signing, it is the object most likely to appear in operator logs, error messages, and `Debug` dumps — contexts readable by parties (log aggregators, monitoring, other node operators, anyone with read access to stdout/journal) who do not have signing privileges.

### Impact Explanation
A party able to read debug/logging output — but not the key store itself — recovers the holder's full FROST secret share `s_i`. Combined with `t - 1` other compromised shares (or fewer, since the share is interpolated for signing and `original_secret_share` is the un-interpolated share), this enables reconstruction of the group secret and arbitrary signature forgery / theft of funds controlled by the threshold key. This is direct secret disclosure through an accessor with weaker access control than the secret requires — exactly the `pg_user_mappings` pattern of CVE-2017-7486 (USING a foreign server != entitlement to its password; reading logs != entitlement to the key share).

### Likelihood Explanation
`Debug` output of `ThresholdKeys` requires some code path in an integrating binary (or an error type embedding the keys) to format it — a common pattern (`unwrap`, `expect` on `Result<_, ThresholdKeys>`-adjacent values, `tracing`/`log` spans, dump-on-panic handlers). The exposure is silent and persistent: once logged, the share sits in whatever log storage exists. Likelihood is conditional on integrator behavior, so the severity is bounded to Medium, but the secrecy contract the rest of the file enforces (manual redacting `Debug` on `ThresholdView`, `Zeroize` everywhere, `finish_non_exhaustive` on `SecretShare`) shows the leak violates the type's own intended guarantees — it is a bug, not a documented behavior. Nothing in `ThresholdKeys`' doc comments warns that `Debug` prints the secret share.

### Recommendation
Replace the derived `Debug` on `ThresholdKeys` with a manual implementation matching `ThresholdView`: print `params`, `scalar`, `offset`, `group_key`, and the verification-share map, and call `finish_non_exhaustive()` so `core.secret_share` is never rendered. Equivalently, give `ThresholdCore` a redacting manual `Debug`. Audit other types that transitively embed `ThresholdKeys`/`ThresholdCore` (`Params` in `crypto/frost/src/sign.rs`, machines holding `keys`) for derived `Debug` that would forward to it.

### Proof of Concept
```rust
// Requires: a completed DKG or ThresholdKeys::<C>::new(...)
let keys: ThresholdKeys<Ristretto> = /* ... */;

// Any of these emit the raw C::F secret share:
let dbg = format!("{keys:?}");            // logs / println! / tracing
// e.g., .field("keys", &keys) in a tracing span, or unwrap_err() on a
// Result containing ThresholdKeys, has the same effect.

// dbg now contains the little-endian/decimal repr of self.core.secret_share,
// recoverable as C::F via C::read_F.
```

Root cause is the `derive(Debug)` at `crypto/dkg/src/lib.rs:291` covering the `core` field (line 295) which contains `secret_share`, while the sibling `ThresholdView` type (lines 316–329) demonstrates the intended redaction the author forgot to apply to `ThresholdKeys`.

### Citations

**File:** crypto/dkg/src/lib.rs (L290-301)
```rust
/// Threshold keys usable for signing.
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

**File:** crypto/dkg/src/lib.rs (L303-329)
```rust
/// View of keys, interpolated and with the expected linear combination taken for usage.
#[derive(Clone)]
pub struct ThresholdView<C: Ciphersuite> {
  interpolation: Interpolation<C::F>,
  scalar: C::F,
  offset: C::F,
  group_key: C::G,
  included: Vec<Participant>,
  secret_share: Zeroizing<C::F>,
  original_verification_shares: HashMap<Participant, C::G>,
  verification_shares: HashMap<Participant, C::G>,
}

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

**File:** crypto/dkg/pedpop/src/lib.rs (L242-250)
```rust
impl<F: PrimeField> fmt::Debug for SecretShare<F> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt.debug_struct("SecretShare").finish_non_exhaustive()
  }
}
impl<F: PrimeField> Zeroize for SecretShare<F> {
  fn zeroize(&mut self) {
    self.0.as_mut().zeroize()
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L542-550)
```rust
impl<C: Ciphersuite> fmt::Debug for BlameMachine<C> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt
      .debug_struct("BlameMachine")
      .field("commitments", &self.commitments)
      .field("encryption", &self.encryption)
      .finish_non_exhaustive()
  }
}
```
