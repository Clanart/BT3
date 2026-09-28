### Title
Crafted FROST preprocess forces identity nonce and crashes Bitcoin transaction signing - (File: networks/bitcoin/src/crypto.rs)

### Summary
`TransactionSignMachine` accepts canonical identity points in remote FROST preprocess commitments. A malicious signer can choose its nonce commitment so the aggregate nonce is the point at infinity. Bitcoin's BIP-340 challenge function then unconditionally extracts the nonce's X coordinate and panics.

### Finding Description
`read_G` validates canonical point encodings but does not reject the identity point. [1](#0-0)  A Schnorr preprocess consists of one nonce commitment containing two points, so untrusted `D` and `E` values are accepted without an identity check. [2](#0-1) 

`BindingFactor::nonces` calculates the aggregate `R` as the sum of every participant's `D` plus `rho * E`. [3](#0-2)  A malicious signer that sees the other commitments can publish `E = identity` and `D = -Σ D_i`; if every supplied `E` is identity, all binding-factor terms vanish and the aggregate nonce becomes identity.

The Schnorr signing implementation passes that aggregate point into `Hram`. [4](#0-3)  Bitcoin's `Hram` calls `x(R)`, and `x` panics when given the point at infinity. [5](#0-4) [6](#0-5) 

### Impact Explanation
An unprivileged signing participant can send a validly encoded preprocess that causes the local Bitcoin transaction-signing task to panic. If the node is configured to terminate on task panics, or if the signing session is not isolated, this becomes a remote denial of service against threshold signing.

### Likelihood Explanation
The attacker only needs to participate in a signing session and know the other participants' nonce commitments before publishing their own. All used values are canonical public encodings, so deserialization succeeds. No malformed byte sequence, invalid curve point, leaked key, or compromised infrastructure is required.

### Recommendation
Reject identity `D` and `E` commitments during `read_preprocess`/`sign`, and explicitly verify that every aggregate nonce is non-identity before calling `sign_share` or `Hram`. Return a `FrostError::InvalidShare`/participant-specific preprocessing error instead of allowing the point to reach `x`.

### Proof of Concept
For a one-input Bitcoin transaction, the remote preprocess is serialized as `D || E`. Set:

```rust
let d_malicious = -(d_local + d_other_1 + ... + d_other_n);
let e_malicious = ProjectivePoint::IDENTITY;

let preprocess_bytes = [
  d_malicious.to_bytes().as_ref(),
  e_malicious.to_bytes().as_ref(),
].concat();
```

Ensure all submitted `E` values are the canonical identity encoding. `Commitments::read` accepts the bytes, `BindingFactor::nonces` computes `R = ΣD + Σ(rho_i E_i) = identity`, and `bitcoin::crypto::Hram::hram` panics at `encoded.x().expect("point at infinity")` when `sign_share` is invoked. [7](#0-6) [8](#0-7)

### Citations

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

**File:** crypto/frost/src/nonce.rs (L74-79)
```rust
  fn read<R: Read>(reader: &mut R, generators: &[C::G]) -> io::Result<NonceCommitments<C>> {
    Ok(NonceCommitments {
      generators: (0 .. generators.len())
        .map(|_| GeneratorCommitments::read(reader))
        .collect::<Result<_, _>>()?,
    })
```

**File:** crypto/frost/src/nonce.rs (L194-208)
```rust
  pub(crate) fn nonces(&self, planned_nonces: &[Vec<C::G>]) -> Vec<Vec<C::G>> {
    let mut nonces = Vec::with_capacity(planned_nonces.len());
    for n in 0 .. planned_nonces.len() {
      nonces.push(Vec::with_capacity(planned_nonces[n].len()));
      for g in 0 .. planned_nonces[n].len() {
        #[allow(non_snake_case)]
        let mut D = C::G::identity();
        let mut statements = Vec::with_capacity(self.0.len());
        #[allow(non_snake_case)]
        for IndividualBinding { commitments, binding_factors } in self.0.values() {
          D += commitments.nonces[n].generators[g].0[0];
          statements
            .push((binding_factors.as_ref().unwrap()[n], commitments.nonces[n].generators[g].0[1]));
        }
        nonces[n].push(D + multiexp_vartime(&statements));
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

**File:** networks/bitcoin/src/crypto.rs (L13-22)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}

/// Convert a non-infinity point to a XOnlyPublicKey (dropping its sign).
///
/// Panics on invalid input.
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
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
