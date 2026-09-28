### Title
Deserialization accepts identity verification shares, yielding an identity (known private key) group key — (File: crypto/dkg/src/lib.rs)

### Summary
The external report describes a guard that only checks the *old* value and never validates that the *new* value is non-zero, so a zero value silently passes. The Serai analog is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`: the verification shares are deserialized with `Ciphersuite::read_G`, which checks canonicality/torsion but explicitly does **not** reject the identity point (that rejection only exists in the FROST-layer `Curve::read_G` override). `ThresholdKeys::new` then computes `group_key` as the interpolation-weighted sum of those shares with no identity check on either the shares or the resulting group key. An attacker who can feed untrusted bytes to `ThresholdKeys::read` can therefore install a `ThresholdKeys` whose group key is the identity — a key whose discrete log (0) is publicly known.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i`, the interpolation variant, the secret share, and then reads `n` verification shares via `<C as Ciphersuite>::read_G` [1](#0-0) . `Ciphersuite::read_G` only enforces a canonical encoding and subgroup membership; identity is accepted [2](#0-1) . The stricter `Curve::read_G` that rejects identity exists only in the FROST crate and is not used here [3](#0-2) .

`ThresholdKeys::new` validates the share *count* and participant indexes but never checks that any verification share — or the resulting group key — is non-identity [4](#0-3) . With `Interpolation::Constant` (t == n, as produced by `musig`) or with all shares set to identity under Lagrange interpolation, `group_key = Σ share_i · factor_i = identity`. `group_key()` then returns `(identity * scalar) + G*offset`; for a freshly deserialized key `scalar = 1, offset = 0`, so the group key is exactly the identity [5](#0-4) , and `read` never serializes the scalar/offset so an attacker-controlled buffer guarantees this state.

### Impact Explanation
A Schnorr/FROST signature under an identity public key is trivially forgeable: `SchnorrSignature::verify` checks `R + c·A − s·G == 0` [6](#0-5) , which for `A = identity` reduces to `R == s·G`. Anyone can pick `s`, set `R = s·G`, and produce a valid signature for this key — a forged signature against a key a victim loaded from attacker-influenced bytes. Additionally, every included participant's interpolated verification share remains identity, so `z_i·G == R_i` lets anyone produce signature shares that pass per-share verification without holding a real share of the key.

### Likelihood Explanation
Reachability requires the attacker to supply the serialized `ThresholdKeys` bytes (e.g., via any path that imports or syncs key material rather than generating it in-process). The `read` path is explicitly a trust boundary for untrusted bytes, and no identity or key-consistency check anywhere in `ThresholdKeys::new` stops it. Exploitation beyond the deserialization primitive requires downstream code to sign or verify with the corrupted view, so severity is Medium.

### Recommendation
Reject identity points in `ThresholdKeys::new` (or in `ThresholdKeys::read` by using a `read_G` variant that bans identity, as `Curve::read_G` does). Also consider verifying `secret_share * C::generator() == verification_shares[i]` on deserialization, and rejecting an identity `group_key` in `view()`/`group_key()`.

### Proof of Concept
```rust
// Forge ThresholdKeys<Ristretto> with all-identity verification shares.
// t = n = 1, i = 1, Interpolation::Lagrange (tag 1), secret_share = arbitrary.
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(1u16.to_le_bytes()); // t
buf.extend(1u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes()); // i
buf.push(1);                    // Interpolation::Lagrange
buf.extend(<Ristretto as Ciphersuite>::F::ONE.to_repr().as_ref()); // secret_share
buf.extend(RistrettoPoint::identity().to_bytes()); // identity verification share

let keys = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice()).unwrap(); // accepted
assert!(bool::from(keys.group_key().is_identity()));

// Forge a Schnorr signature for this group key: pick s, set R = s*G.
let s = <Ristretto as Ciphersuite>::F::random(OsRng);
let sig = SchnorrSignature::<Ristretto> { R: Ristretto::generator() * s, s };
let c = /* challenge per HRAM(context, R, A = identity, msg) */;
assert!(sig.verify(keys.group_key(), c)); // R + c*identity - s*G == 0
```

Root cause confirmed in `crypto/dkg/src/lib.rs:620-631` (`ThresholdKeys::read` → `Ciphersuite::read_G`), `crypto/ciphersuite/src/lib.rs:91-101` (no identity rejection), `crypto/dkg/src/lib.rs:376-378` (unchecked group key), and `crypto/frost/src/curve/mod.rs:123-131` (identity rejection exists only at the FROST curve layer).

### Citations

**File:** crypto/dkg/src/lib.rs (L355-378)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L445-447)
```rust
  pub fn group_key(&self) -> C::G {
    (self.core.group_key * self.scalar) + (C::generator() * self.offset)
  }
```

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
  }
```

**File:** crypto/frost/src/curve/mod.rs (L123-131)
```rust
  /// Read a point from a reader, rejecting identity.
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```

**File:** crypto/schnorr/src/lib.rs (L88-109)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
```
