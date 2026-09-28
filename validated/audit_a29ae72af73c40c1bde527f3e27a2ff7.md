### Title
Empty Schnorr aggregate verifies as a valid signature - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero signatures when `s` is zero. A serialized proof consisting of `0` signatures followed by a canonically encoded zero scalar therefore verifies successfully against an empty signer list, even though `SchnorrAggregator::complete` explicitly refuses to produce an empty aggregate. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` accepts a four-byte little-endian count of zero and then reads `s` without requiring `Rs` to be non-empty. [3](#0-2)  During verification, matching an empty `keys_and_challenges` list produces only the pair `(-s, G)`. [1](#0-0)  When `s == 0`, this is `-0 * G`, whose multiexponentiation result is identity, so verification returns true. [4](#0-3) 

### Impact Explanation
Any verifier path that treats `SchnorrAggregate::verify` as proof that a non-empty signer set endorsed a message can accept a forged aggregate signature for arbitrary `dst` data when the supplied signer list is empty. The signing-side API recognizes the empty aggregate as invalid and returns `None`, but the verification-side API fails to enforce the same invariant. [2](#0-1) 

### Likelihood Explanation
Exploitation requires a caller to invoke verification with an empty signer list, so protocol layers that already enforce quorum membership are not affected. However, the malformed object is fully public-input controlled, canonical, short, and requires no secret knowledge or computation beyond serialization.

### Recommendation
Reject `self.Rs.is_empty()` at the start of `SchnorrAggregate::verify` and preferably reject a zero `Rs` count in `SchnorrAggregate::read`. Add a regression test asserting that `verify(dst, &[])` returns false for an empty aggregate with `s = 0`.

### Proof of Concept
For a 32-byte scalar ciphersuite such as Ristretto or secp256k1, the forged serialization is:

```text
00 00 00 00                                      // Rs length = 0
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00  // s = 0
```

Conceptually:

```rust
let forged = SchnorrAggregate::<C> {
  Rs: vec![],
  s: C::F::ZERO,
};

assert!(forged.verify(b"arbitrary destination", &[]));
```

The equation reduces to `0 * G == identity`, causing the verifier to accept despite no signer having contributed a nonce or signature share. [5](#0-4)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```

**File:** crypto/schnorr/src/aggregate.rs (L127-145)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }

    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }

    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```

**File:** crypto/schnorr/src/aggregate.rs (L175-184)
```rust
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
    for i in 0 .. self.sigs.len() {
      aggregate.Rs.push(self.sigs[i].R);
      aggregate.s += self.sigs[i].s * weight::<_, C::F>(&mut self.digest);
    }
```

**File:** crypto/multiexp/src/lib.rs (L183-190)
```rust
  match algorithm(pairs.len()) {
    Algorithm::Null => Group::identity(),
    Algorithm::Single => pairs[0].1 * pairs[0].0,
    // These functions panic if called without any pairs
    Algorithm::Straus(window) => straus(pairs, window),
    Algorithm::Pippenger(window) => pippenger(pairs, window),
  }
}
```
