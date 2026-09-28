### Title
Malformed identity nonce commitments panic BIP-340 signing - (File: `networks/bitcoin/src/crypto.rs`)

### Summary
An untrusted participant can submit canonically encoded identity points as FROST nonce commitments. The parser accepts identity points, while Bitcoin’s BIP-340 challenge function panics when the aggregate nonce `R` is infinity.

### Finding Description
`TransactionSignMachine::read_preprocess` delegates to each input’s `AlgorithmSignMachine::read_preprocess`, which parses each participant’s nonce commitments without rejecting the identity element. For the Bitcoin Schnorr algorithm, each nonce commitment consists of two public points. During `sign`, FROST combines those commitments into the aggregate nonce `R`, then invokes the Bitcoin `Hram`. `Hram::hram` calls `x(R)`, whose documentation and implementation panic when `R` is the point at infinity. Thus, a malicious participant can provide crafted commitment points that make the aggregate nonce infinity and cause an uncaught panic during signing.

### Impact Explanation
A peer can abort a Bitcoin threshold-signing operation and potentially crash the calling process by sending crafted public preprocess bytes. This is a reachable denial of service against otherwise valid participants.

### Likelihood Explanation
The malicious party only controls a public preprocess message. Canonical encodings are accepted by `C::read_G`; the vulnerable path does not perform an identity check before passing the resulting aggregate point to `x(R)`.

### Recommendation
Reject identity nonce commitments during `read_preprocess`, and defensively reject identity `R`/`A` before calling `Hram::hram`. `Schnorr::sign_share`, `verify`, and `verify_share` should return an error or invalid-signature result rather than panic.

### Proof of Concept
1. Construct a `TransactionSignMachine` for at least one Bitcoin input.
2. Provide a peer preprocess containing canonical identity encodings for that input’s two nonce commitments.
3. Arrange the commitments so the FROST binding formula produces aggregate `R = identity`.
4. Call `sign`.
5. The call reaches `Hram::hram`, which calls `x(R)`; `x` unwraps the missing x-coordinate for infinity and panics.

Relevant code: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** networks/bitcoin/src/crypto.rs (L12-16)
```rust
/// Panics on invalid input.
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

**File:** networks/bitcoin/src/wallet/send.rs (L351-352)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
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
