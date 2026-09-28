### Title
`ThresholdKeys::read` accepts `Interpolation::Constant` coefficients of zero, allowing a degenerate (identity) group key - (File: crypto/dkg/src/lib.rs)

### Summary
The referenced bug is a parameter that must be `> 0` but is only upper-bound checked, letting a zero value produce a degenerate construction. The Serai analog: `ThresholdKeys::read` deserializes an `Interpolation::Constant(Vec<C::F>)` whose scalar coefficients are read verbatim from untrusted bytes with `C::read_F` and are never validated to be non-zero. `ThresholdKeys::new` then derives `group_key` as `Σ verification_shares[i] * interpolation_factor(i)`, where the factor is simply `c[i-1]`. An attacker who supplies all-zero coefficients obtains `ThresholdKeys` whose `group_key()` is the identity point — i.e., the threshold private key is `0`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`ThresholdKeys::read` supports two interpolation tags: `0` for `Constant` (reading `n` arbitrary scalars) and `1` for `Lagrange`. For the `Constant` variant the only check applied is `params.t() == params.n()` inside `ThresholdKeys::new`; the coefficients themselves are never checked for zero or consistency. The group key is computed purely as a linear combination of the verification shares weighted by these coefficients. With `c = [0, …, 0]`, `group_key` becomes the additive identity regardless of the verification shares read from the stream. `view()` produces a `secret_share` of `c_i * secret_share (+ offset)` = `0`/`offset`, and `dkg_recovery::recover_key` would reconstruct `0`, consistent with the identity group key. [4](#0-3) [5](#0-4) [6](#0-5) 

### Impact Explanation
A `ThresholdKeys` whose group key is the identity point corresponds to a group private key of `0`. Schnorr/FROST signatures under an identity public key are publicly forgeable: for any nonce `r`, `(R = r·G, s = r)` satisfies `s·G = R + c·Identity = R`. Consequently, any funds/outputs the system attributes to this `group_key` (e.g., a Bitcoin/Taproot output derived from it) are spendable by anyone without a single valid share, and shares held by honest participants still "verify" against the deserialized verification shares while the actual key is public. This mirrors the reported issue: a zero-valued parameter silently collapses the construction into a degenerate, attacker-benign form instead of being rejected.

### Likelihood Explanation
The trigger is an attacker-controlled byte buffer passed to `ThresholdKeys::read` (explicitly in scope). Any flow where key material is restored from remote/untrusted storage, gossiped, or provided by a counterparty reaches this path. The malicious encoding is trivially constructible: valid curve ID, `t = n`, tag `0`, `n` zero scalar encodings, a zero secret share, and `n` arbitrary (e.g., identity or `G`) verification-share points. It passes all deserialization checks and produces `Ok(ThresholdKeys)` — no error is raised because no lower-bound/consistency check on the coefficients exists.

### Recommendation
Either reject the `Constant` interpolation variant entirely in `ThresholdKeys::read` (it is only ever produced internally by MuSig and never legitimately serialized), or validate that every `Constant` coefficient is non-zero and that the interpolated group key is non-identity. Additionally, `ThresholdKeys::new` should reject a computed `group_key` equal to `C::G::identity()`.

### Proof of Concept
```rust
// crypto/dkg — conceptual PoC for C = Ristretto
let n: u16 = 1; // t = n = 1 satisfies the Constant t == n rule
let mut serialized = vec![];
serialized.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
serialized.extend(Ristretto::ID);
serialized.extend(n.to_le_bytes());        // t = 1
serialized.extend(n.to_le_bytes());        // n = 1
serialized.extend(1u16.to_le_bytes());     // i = 1
serialized.push(0u8);                      // Interpolation::Constant
// ONE zero coefficient — never checked for zero
serialized.extend(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref());
serialized.extend(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref()); // secret_share = 0
serialized.extend(<Ristretto as Ciphersuite>::generator().to_bytes().as_ref()); // any v-share

let keys = ThresholdKeys::<Ristretto>::read(&mut serialized.as_slice()).unwrap();
assert!(bool::from(keys.group_key().is_identity())); // group key is the identity point
// Any Schnorr signature (R = r*G, s = r) now verifies for this group key:
// s*G == R + H(...)*Identity  — forgery requires no key share at all.
```

### Citations

**File:** crypto/dkg/src/lib.rs (L226-228)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
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

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L494-521)
```rust
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }

    /*
      The offset is included by adding it to the participant with the lowest ID.

      This is done after interpolating to ensure, regardless of the method of interpolation, that
      the method of interpolation does not scale the offset. For Lagrange interpolation, we could
      add the offset to every key share before interpolating, yet for Constant interpolation, we
      _have_ to add it as we do here (which also works even when we intend to perform Lagrange
      interpolation).
    */
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
```

**File:** crypto/dkg/src/lib.rs (L604-616)
```rust
    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };
```

**File:** crypto/dkg/recovery/src/lib.rs (L73-82)
```rust
  let mut res: Zeroizing<_> =
    first_keys.view(included.clone()).map_err(RecoveryError::DkgError)?.secret_share().clone();
  for keys in keys {
    *res.deref_mut() +=
      keys.view(included.clone()).map_err(RecoveryError::DkgError)?.secret_share().deref();
  }

  if (C::generator() * res.deref()) != first_keys.group_key() {
    Err(RecoveryError::Failure)?;
  }
```
