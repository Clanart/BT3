### Title
Secret key-tweak scalars leaked via `Debug` formatting of `ThresholdKeys`/`ThresholdView` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys` derives `Debug`, and `ThresholdView` has a manual `Debug` impl that explicitly prints `scalar` and `offset`. Both are secret `C::F` field elements applied on top of the threshold secret share to implement "account and privacy schemes". Any logging, error capture, or debug formatting of these structs discloses the tweak values, analogously to CVE-2021-47216 (a kernel pointer leaked via `%lx` formatting of a value that should have remained opaque).

### Finding Description
The codebase is careful to hide secret material in `Debug` impls: `ThresholdCore`'s manual impl uses `finish_non_exhaustive()` to omit `secret_share` (crypto/dkg/src/lib.rs:266-276), `SecretShareMachine`/`KeyMachine`/`BlameMachine`/`Encryption` omit `coefficients`, `secret`, and `enc_key`, and `SecretShare` prints nothing at all (crypto/dkg/pedpop/src/lib.rs:242-246). However:

- `ThresholdKeys` uses `#[derive(Clone, Debug, Zeroize)]` (crypto/dkg/src/lib.rs:291), so its `scalar: C::F` and `offset: C::F` fields (lines 298-300) are formatted with the field element's `Debug` (e.g., `Scalar`'s derived `Debug` in crypto/dalek-ff-group/src/lib.rs:178-179 prints the inner dalek scalar, exposing the value).
- `ThresholdView`'s manual impl explicitly includes `.field("scalar", &self.scalar)` and `.field("offset", &self.offset)` (crypto/dkg/src/lib.rs:321-322).

The doc comments state these values are "ephemeral" tweaks for "account and privacy schemes" (crypto/dkg/src/lib.rs:393-417). For privacy-oriented usages the scalar/offset is a secret (its disclosure de-anonymizes the tweaked key and, combined with other data, can reveal the effective private key `scalar * share + offset` relationships). `ThresholdKeys` is reachable through `ThresholdKeys::read`, `scale`, `offset`, and `view` from in-scope crates.

### Impact Explanation
Formatting a `ThresholdKeys` or `ThresholdView` (a routine occurrence via `{:?}` in logs, `unwrap`/`expect` on enclosing structs, error traces, or `AdditionalBlameMachine`'s derived `Debug` printing its inner `BlameMachine` — crypto/dkg/pedpop/src/lib.rs:636-637 — which currently does not leak `result` because `result` isn't formatted, but any `ThresholdKeys` field elsewhere is) writes the private tweak scalars in plaintext. Disclosure of `scalar`/`offset` breaks the privacy property of the tweak and, where the tweak is derived from a secret (e.g., a private account scalar), leaks that secret. Like the CVE, secret material is exposed simply because a value was printed rather than kept opaque.

### Likelihood Explanation
Medium. Triggering requires only that an integrator format these structs — a plausible and even likely action, since `Debug` is implemented precisely to permit it, and `ThresholdKeys` is embedded in processor-side structures (`#[derive(Debug)] KeyGen` holds `ThresholdKeys` values, processor/src/key_gen.rs:136-144). The secrecy of scalar/offset depends on the scheme; for public tweaks (e.g., hash-derived TapTweak offsets) the leak is harmless, capping severity at Medium.

### Recommendation
Give `ThresholdKeys` a manual `Debug` impl that omits `scalar` and `offset` (using `finish_non_exhaustive()`), and remove `.field("scalar", ...)`/`.field("offset", ...)` from `ThresholdView`'s impl, consistent with how `ThresholdCore` already hides `secret_share`. If visibility is needed for diagnostics, print only whether they differ from `ONE`/`ZERO`.

### Proof of Concept
```rust
// In-scope: crypto/dkg. With any Ciphersuite C (e.g., Ristretto):
let keys = ThresholdKeys::<C>::new(params, Interpolation::Lagrange, secret_share, shares)?
  .scale(secret_account_scalar).unwrap()  // secret tweak applied
  .offset(secret_offset);

// Any formatting leaks the secret scalar and offset:
let leaked = format!("{keys:?}");
// `leaked` now contains the plaintext C::F representations of
// `scalar` and `offset` (and a ThresholdView's Debug prints them verbatim).
assert!(leaked.contains("scalar"));
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** crypto/dalek-ff-group/src/lib.rs (L177-180)
```rust
/// Wrapper around the dalek Scalar type.
#[derive(Clone, Copy, PartialEq, Eq, Default, Debug, Zeroize)]
pub struct Scalar(pub DScalar);
deref_borrow!(Scalar, DScalar);
```

**File:** crypto/dkg/pedpop/src/lib.rs (L242-246)
```rust
impl<F: PrimeField> fmt::Debug for SecretShare<F> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt.debug_struct("SecretShare").finish_non_exhaustive()
  }
}
```
