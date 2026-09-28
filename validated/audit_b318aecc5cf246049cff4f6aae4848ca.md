### Title
Malformed `ThresholdKeys` can encode an identity group key and panic BIP-340 signing - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts untrusted verification shares whose interpolated sum is the identity point. The resulting malformed key can be deserialized successfully, but Bitcoin’s Schnorr algorithm later calls `x(A)` on that identity group key, which unconditionally panics because an identity point has no x-coordinate. An attacker who can supply the serialized `ThresholdKeys` bytes can therefore crash the process when signing is attempted.

### Finding Description
`ThresholdKeys::read` deserializes attacker-controlled verification shares with `C::read_G`, then passes them to `ThresholdKeys::new`. [1](#0-0)  `Ciphersuite::read_G` checks canonical encoding but does not reject the identity point. [2](#0-1)  `ThresholdKeys::new` computes the group key as the interpolated sum of the first `t` verification shares and stores the result without checking whether it is identity. [3](#0-2) 

During signing, `Schnorr::sign_share` passes this group key to the algorithm’s HRAM implementation. [4](#0-3)  The Bitcoin BIP-340 HRAM calls `x(A)` on the group key. [5](#0-4)  `x` panics whenever its input is the point at infinity. [6](#0-5) 

### Impact Explanation
This is a remotely triggerable denial of service in deployments that deserialize untrusted `ThresholdKeys` bytes. The malformed encoding is syntactically valid and survives `ThresholdKeys::read`; the process subsequently panics when it tries to produce a Bitcoin Schnorr signature. Because the panic occurs inside signing rather than being returned as an `io::Error` or `FrostError`, callers cannot handle it through the normal error paths.

### Likelihood Explanation
The attacker only needs to control the serialized `ThresholdKeys` input. For example, with Lagrange interpolation and `t = n = 2`, participant 1 has interpolation factor `2` and participant 2 has factor `-1`. Encoding verification shares `G` and `2G` produces `2G - 2G = identity`, causing deserialization to create a threshold key whose group key is the point at infinity. Any attempt to sign with Bitcoin’s `Schnorr` algorithm then reaches the documented panic path.

### Recommendation
Reject identity group keys when constructing or deserializing `ThresholdKeys`. After computing `group_key` in `ThresholdKeys::new`, return a `DkgError` if it is the identity. Additionally, consider making Bitcoin’s HRAM return a signature/signing failure rather than panic on identity inputs, although deserialization-time rejection is the primary defense.

### Proof of Concept
Conceptually, for `Secp256k1`:

```text
serialized ThresholdKeys:
  len(C::ID) || C::ID
  t = 2
  n = 2
  i = 1 or 2
  interpolation = Lagrange (tag 1)
  secret_share = any canonical scalar
  verification_share[1] = G
  verification_share[2] = 2G
```

`ThresholdKeys::read` accepts this encoding. `ThresholdKeys::new` computes:

```text
group_key = 2 * G + (-1) * 2G = identity
```

Passing the resulting `ThresholdKeys<Secp256k1>` into Bitcoin’s FROST `Schnorr` signing path causes `Hram::hram` to evaluate `x(A)` with `A = identity`, triggering the `expect("point at infinity")` panic in `x`.

### Citations

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

**File:** crypto/frost/src/algorithm.rs (L201-210)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
```

**File:** networks/bitcoin/src/crypto.rs (L13-16)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-72)
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
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
      // end to sign as `-r + cx`
      <_>::conditional_select(&c, &-c, needs_negation(R))
```
