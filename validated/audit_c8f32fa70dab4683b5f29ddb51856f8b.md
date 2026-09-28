### Title
Deserialization creates a valid empty aggregate Schnorr signature - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero `R` values and a zero scalar. `SchnorrAggregate::verify` then checks only that the number of `R`s equals the supplied signer list and computes `0 * G`, which is the identity. Consequently, a 36-byte encoding is accepted as a valid aggregate signature for an empty signer set.

### Finding Description
The signing-side API correctly prevents this state: `SchnorrAggregator::complete` returns `None` when no signatures were aggregated [1](#0-0) . The untrusted decoding path does not enforce the same invariant and permits `Rs.len() == 0` [2](#0-1) .

During verification, no signer keys are required for a signer list of length zero. The verifier builds only the pair `(-s, G)`; with the canonical zero scalar this is the identity point, so verification succeeds [3](#0-2) .

This also reaches the production aggregate-verification wrapper: `Validators::verify_aggregate` rejects only a length mismatch and otherwise passes an empty `signers` slice to `SchnorrAggregate::verify` [4](#0-3) .

### Impact Explanation
An unauthenticated party can forge an aggregate signature for an empty signer set. In any caller that treats `verify`/`verify_aggregate` as the authoritative authentication result without independently requiring at least one signer or sufficient voting weight, an empty attestation may be accepted as signed.

The issue does not by itself bypass a correctly enforced external threshold/quorum check. Its security boundary is the aggregate-signature verifier accepting a signature that the legitimate aggregator can never produce.

### Likelihood Explanation
Exploitation requires the caller to supply or authorize an empty signer set. The serialized payload is only 36 bytes and requires no secret material, nonce, proof work, or participant access. The likelihood is therefore limited by application-level validation of the signer set rather than by cryptographic difficulty.

### Recommendation
Reject empty aggregate signatures during both deserialization and verification. Add an explicit `self.Rs.is_empty()` / `keys_and_challenges.is_empty()` check returning `false` or an error, matching the producer-side `complete()` behavior. Also add a regression test asserting that a zero-length `Rs` encoding with `s = 0` cannot verify.

### Proof of Concept
For a ciphersuite whose scalar encoding is 32 bytes, encode:

```text
u32_le Rs_length = 0
F scalar s       = 0
```

The complete payload is:

```text
00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

Conceptual Rust test:

```rust
let encoded = [0u8; 36];
let aggregate =
  SchnorrAggregate::<Secp256k1>::read(&mut encoded.as_slice()).unwrap();

assert!(aggregate.verify(b"valid domain separator", &[]));
```

`verify` compares two empty lists, evaluates only `-(0 * G)`, observes the identity point, and returns `true`.

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L76-88)
```rust
  /// Read a SchnorrAggregate from something implementing Read.
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

**File:** crypto/schnorr/src/aggregate.rs (L174-185)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
    for i in 0 .. self.sigs.len() {
      aggregate.Rs.push(self.sigs[i].R);
      aggregate.s += self.sigs[i].s * weight::<_, C::F>(&mut self.digest);
    }
    Some(aggregate)
```

**File:** coordinator/tributary/src/tendermint/mod.rs (L207-228)
```rust
    let Ok(aggregate) = SchnorrAggregate::<Ristretto>::read::<&[u8]>(&mut sig.as_slice()) else {
      return false;
    };

    if signers.len() != aggregate.Rs().len() {
      return false;
    }

    let mut challenges = vec![];
    for (key, nonce) in signers.iter().zip(aggregate.Rs()) {
      challenges.push(challenge(self.genesis, *key, nonce.to_bytes().as_ref(), msg));
    }

    aggregate.verify(
      DST,
      signers
        .iter()
        .zip(challenges)
        .map(|(s, c)| (<Ristretto as Ciphersuite>::read_G(&mut s.as_slice()).unwrap(), c))
        .collect::<Vec<_>>()
        .as_slice(),
    )
```
