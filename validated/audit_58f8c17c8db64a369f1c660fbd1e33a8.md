### Title
Missing minimum-length check allows an empty aggregate signature to verify - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an encoded aggregate containing zero nonces, while `SchnorrAggregate::verify` accepts that object when given an empty `keys_and_challenges` list. A serialized aggregate with zero signers and `s = 0` therefore verifies successfully even though `SchnorrAggregator::complete` refuses to produce an aggregate from zero signatures. This is analogous to the missing lower-bound validation in the referenced issue.

### Finding Description
`SchnorrAggregate::read` reads an attacker-controlled `u32` count and then accepts `0` as a valid number of `Rs`, followed by any canonical scalar for `s`. [1](#0-0)  Verification only checks that `self.Rs.len() == keys_and_challenges.len()`, queues one statement for each listed key, adds `-s * G`, and accepts if the multiexp is identity. [2](#0-1)  With both lists empty and `s = 0`, the only queued statement is the identity, so verification succeeds. The aggregation API explicitly treats an empty input as not producing a signature by returning `None`, establishing that a zero-signer aggregate is unintended. [3](#0-2) 

### Impact Explanation
An unauthenticated party can supply bytes that deserialize to a syntactically valid `SchnorrAggregate` which verifies over an empty signer set. If an integrator calls `verify` with a dynamically selected signer/challenge list and fails to reject an empty list before calling this API, the attacker can produce a forged aggregate signature without knowing any private key. The cryptographic issue is the accepting verifier formula for an unauthorized zero-signer statement, not merely a malformed encoding rejection problem.

### Likelihood Explanation
The attack requires a caller to invoke `verify` with no public keys/challenges, so exploitation depends on integrator flow rather than being universally exploitable for nonempty signer sets. However, the affected functions are public, the parser accepts untrusted serialized data, and the required proof is a fixed byte string: a four-byte zero count followed by the zero scalar encoding.

### Recommendation
Reject empty aggregates in `SchnorrAggregate::read`, and independently reject `keys_and_challenges.is_empty()` in `verify`. This should be enforced in verification even if parsing is hardened, because callers can construct `SchnorrAggregate` within the crate or through future constructors. A regression test should assert that reading `0 || encode(0)` fails and that verifying an empty statement fails.

### Proof of Concept
```rust
use ciphersuite::{Ciphersuite, group::ff::PrimeField};
use schnorr::aggregate::SchnorrAggregate;

fn forged_empty_aggregate<C: Ciphersuite>() {
  let mut bytes = 0u32.to_le_bytes().to_vec();
  bytes.extend_from_slice(C::F::ZERO.to_repr().as_ref());

  let aggregate = SchnorrAggregate::<C>::read(&mut bytes.as_slice()).unwrap();

  // Succeeds even though no public key, message challenge, or nonce was supplied.
  assert!(aggregate.verify(b"example-dst", &[]));
}
```

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-87)
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
