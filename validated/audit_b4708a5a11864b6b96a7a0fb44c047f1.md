### Title
`ThresholdKeys::read` / `ThresholdKeys::new` accept identity verification shares, yielding a permanent identity group key - (File: crypto/dkg/src/lib.rs)

### Summary
The referenced bug class is a setter that stores a critical key without rejecting the zero value, permanently destroying the capability it controls. The Serai analog lives in the DKG crate: `ThresholdKeys::new` derives `group_key` from the supplied `verification_shares` but never rejects identity shares, and `ThresholdKeys::read` deserializes those shares with `<C as Ciphersuite>::read_G` — not the identity-rejecting `Curve::read_G` used elsewhere in FROST — so untrusted bytes containing identity (or adversarially crafted) verification shares produce `ThresholdKeys` whose `group_key` is the identity point. [1](#0-0) [2](#0-1) 

### Finding Description
`ThresholdKeys::read` reads `n` verification shares with `Ciphersuite::read_G`, which only enforces canonical encoding, not non-identity [3](#0-2) . FROST's own `Curve::read_G` exists precisely because identity points must be rejected, yet it is not used here [4](#0-3) . `ThresholdKeys::new` then computes `group_key = sum(verification_shares[i] * interpolation_factor(i))` over participants `1..=t` with no identity check on either the inputs or the result [5](#0-4) . Supplying shares that interpolate to the identity (e.g., all identity points, which requires no discrete-log work) yields `group_key() == identity`. Signatures under an identity group key are universally forgeable (`R = s*G`, any `s` satisfies verification against identity public key in Schnorr/FROST verification), or equivalently represent a key with no known private key — the multisig is permanently unusable/brickable, mirroring the "zero minting_multisig" loss.

### Impact Explanation
Any component that ingests serialized `ThresholdKeys` from untrusted bytes (the explicitly permitted `ThresholdKeys::read` surface) can be handed keys whose group key is the identity. Funds addressed to that key are unspendable (no real secret exists), or — worse — anyone can forge signatures/"proofs of ownership" under the identity key wherever it is used as a verifier key, without any threshold of signers. Either outcome is permanent for that key material, matching the report's "lose it forever, without the option to set a new one" impact.

### Likelihood Explanation
An attacker does not need a hash collision or key knowledge: `read_F`/`read_G` accept canonical encodings of zero scalars and the identity point, and `ThresholdKeys::new` performs only count/index validation (share count == `n`, participant ≤ `n`) plus an interpolation-mode consistency check — never value validation [6](#0-5) . The only prerequisite is a code path that deserializes `ThresholdKeys` from attacker-influenced bytes, which is within the stated public-input surface.

### Recommendation
In `ThresholdKeys::new` (and therefore transitively `ThresholdKeys::read`), reject identity verification shares and assert the computed `group_key` is non-identity, analogously to `Curve::read_G` [4](#0-3) . Additionally consider rejecting `secret_share == 0` in `ThresholdKeys::new` for symmetry with `random_nonzero_F` [7](#0-6) .

### Proof of Concept
```rust
// crypto/dkg: craft bytes for ThresholdKeys::read where every
// verification share is the identity point encoding.
let mut buf = vec![];
buf.extend((C::ID.len() as u32).to_le_bytes());
buf.extend(C::ID);
buf.extend(t.to_le_bytes());          // e.g. t = 2
buf.extend(n.to_le_bytes());          // e.g. n = 3
buf.extend(i.to_le_bytes());          // valid participant, e.g. 1
buf.push(1);                          // Interpolation::Lagrange
buf.extend(C::F::ONE.to_repr().as_ref()); // secret_share = 1
for _ in 1..=n {
    buf.extend(C::G::identity().to_bytes().as_ref()); // identity shares accepted by Ciphersuite::read_G
}
let keys = ThresholdKeys::<C>::read(&mut &buf[..]).unwrap();
// group_key = sum(identity * l_i) = identity -> unspendable / forgeable key
assert!(bool::from(keys.group_key().is_identity()));
```

*Caveat: whether the resulting identity key is exploitable as a forgery target versus merely a permanently unspendable key depends on the consumer; both satisfy the accepted impact categories (funds not spendable / forged signature), but I could not fully trace a specific downstream consumer within the iteration budget.*

### Citations

**File:** crypto/dkg/src/lib.rs (L355-365)
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
```

**File:** crypto/dkg/src/lib.rs (L376-390)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
```

**File:** crypto/dkg/src/lib.rs (L620-630)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
```

**File:** crypto/ciphersuite/src/lib.rs (L62-68)
```rust
  fn random_nonzero_F<R: RngCore + CryptoRng>(rng: &mut R) -> Self::F {
    let mut res;
    while {
      res = Self::F::random(&mut *rng);
      res.ct_eq(&Self::F::ZERO).into()
    } {}
    res
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
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
```

**File:** crypto/frost/src/curve/mod.rs (L125-131)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
