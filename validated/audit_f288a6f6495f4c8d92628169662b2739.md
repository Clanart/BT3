### Title
`ThresholdKeys::read` accepts all-identity verification shares and a zero secret share, yielding a point-at-infinity group key which makes any received funds permanently unspendable - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes untrusted bytes into `ThresholdKeys` without checking that the resulting key material is semantically valid. The verification shares are read with `Ciphersuite::read_G`, which enforces only canonical encoding and explicitly accepts the identity point. A zero `secret_share` and all-identity `verification_shares` are accepted, producing a `group_key` equal to the point at infinity — the exact analog of an uninitialized/`rewards_distributor` zero address.

### Finding Description
- `Ciphersuite::read_G` only rejects non-canonical encodings; identity passes. `Curve::read_G` (FROST) additionally rejects identity, but `ThresholdKeys::read` deliberately calls `<C as Ciphersuite>::read_G` for the `n` verification shares, bypassing the identity check. `secret_share` is read via `C::read_F` with no non-zero check. [1](#0-0) [2](#0-1) 
- `ThresholdKeys::new` validates participant indexes and `t`/`n` consistency, but never checks that any verification share is non-identity or that the computed `group_key` is non-identity. With all shares identity, `group_key = Σ share_i · λ_i = identity` regardless of the interpolation factors. [3](#0-2) 
- The code itself acknowledges the gap: `AlgorithmSignatureMachine::complete` notes "the only known way to cause this ... is to deserialize a semantically invalid FrostKeys". [4](#0-3) 
- Downstream consumers assume a real key. `Scanner::new(key)` calls `p2tr_script_buf(key)` on the group key, and `tweak_keys` calls `needs_negation(&keys.group_key())` and `TapTweakHash::hash(&keys.group_key().to_bytes()[1..])` — operations on the point at infinity that either return `None`/panic or produce a "tweaked" identity. `Schnorr::verify`/`Hram::hram` in bitcoin-serai explicitly document they "may panic if called with nonces/a group key which are the point at infinity". [5](#0-4) [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (a listed untrusted-bytes API) can install a `ThresholdKeys` whose `group_key` is the point at infinity. If that key is used to derive deposit addresses, funds sent to it are permanently unspendable — the direct Serai analog of rewards being sent to the zero address. Additionally, an all-zero secret share means the "signer" contributes `s = nonce` only, and share-verification statements degenerate, which can drive the signing pipeline into the `InternalError` path or panics in BIP-340 `x()`/`needs_negation` on infinity, causing loss of liveness.

### Likelihood Explanation
Requires an attacker to supply the serialized `ThresholdKeys` blob, so the exposure depends on deployment (e.g., key material loaded from data an external party influenced). The exploit is deterministic once reached — no probability assumptions needed since canonical encodings of identity/zero always deserialize.

### Recommendation
In `ThresholdKeys::new` (which also covers `read`), reject identity verification shares (`share.is_identity()`) and reject `group_key.is_identity()` after interpolation. Optionally reject `secret_share.is_zero()`. Alternatively, use the identity-rejecting `Curve::read_G`-style read for verification shares during deserialization.

### Proof of Concept
```rust
// C: any Ciphersuite used with frost/dkg, e.g. Ristretto or Secp256k1
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(1u16.to_le_bytes()); // t = 1
buf.extend(1u16.to_le_bytes()); // n = 1
buf.extend(1u16.to_le_bytes()); // i = 1
buf.push(1);                    // Interpolation::Lagrange
buf.extend(C::F::ZERO.to_repr().as_ref());        // secret_share = 0
buf.extend(C::G::identity().to_bytes().as_ref()); // verification_share = identity
let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap(); // SUCCEEDS
assert!(bool::from(keys.group_key().is_identity())); // zero-address analog
```
`keys.view(vec![Participant::new(1).unwrap()])` then yields a view whose `group_key` is infinity; `SchnorrSignature { R: identity, s: 0 }` satisfies `batch_statements` trivially against that key, and bitcoin-serai `Hram::hram`/`Schnorr::verify` may panic on it.

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

**File:** crypto/dkg/src/lib.rs (L618-623)
```rust
    let secret_share = Zeroizing::new(C::read_F(reader)?);

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

**File:** crypto/frost/src/sign.rs (L491-494)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```

**File:** networks/bitcoin/src/wallet/mod.rs (L46-75)
```rust
pub fn tweak_keys(keys: ThresholdKeys<Secp256k1>) -> ThresholdKeys<Secp256k1> {
  // Adds the unspendable script path per
  // https://github.com/bitcoin/bips/blob/master/bip-0341.mediawiki#cite_note-23
  let keys = {
    use k256::elliptic_curve::{
      bigint::{Encoding, U256},
      ops::Reduce,
      group::GroupEncoding,
    };
    let tweak_hash = TapTweakHash::hash(&keys.group_key().to_bytes().as_slice()[1 ..]);
    /*
      https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki#cite_ref-13-0 states how the
      bias is negligible. This reduction shouldn't ever occur, yet if it did, the script path
      would be unusable due to a check the script path hash is less than the order. That doesn't
      impact us as we don't want the script path to be usable.
    */
    keys.offset(<Secp256k1 as Ciphersuite>::F::reduce(U256::from_be_bytes(
      *tweak_hash.to_raw_hash().as_ref(),
    )))
  };

  let needs_negation = needs_negation(&keys.group_key());
  keys
    .scale(<_ as subtle::ConditionallySelectable>::conditional_select(
      &Scalar::ONE,
      &-Scalar::ONE,
      needs_negation,
    ))
    .expect("scaling keys by 1 or -1 yet interpreted as 0?")
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-69)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);

      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
```

**File:** networks/bitcoin/src/crypto.rs (L76-84)
```rust
  /// BIP-340 Schnorr signature algorithm.
  ///
  /// This may panic if called with nonces/a group key which are the point at infinity (which have
  /// a negligible probability for a well-reasoned caller, even with malicious participants
  /// present).
  ///
  /// `verify`, `verify_share` MUST be called after `sign_share` is called. Otherwise, this library
  /// MAY panic.
  #[derive(Clone)]
```
