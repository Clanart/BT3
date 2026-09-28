### Title
Empty deserialized Schnorr aggregate verifies as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregator::complete` intentionally refuses to create an aggregate when no signatures were provided, but `SchnorrAggregate::read` permits an empty `Rs` vector and `SchnorrAggregate::verify` accepts it when `keys_and_challenges` is empty. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
An attacker can deserialize an aggregate containing zero nonces and `s = 0`. [2](#0-1)  Verification only checks that the number of nonces equals the number of supplied key/challenge pairs, so two empty lists pass that check. [4](#0-3)  It then evaluates the single statement `0 * G`, which is the identity, and returns `true`. [5](#0-4)  The normal aggregation API treats an empty aggregate as invalid by returning `None`, showing this result is not intended to be representable. [1](#0-0) 

### Impact Explanation
A protocol that receives an aggregate from untrusted bytes and verifies it against an attacker-controlled or dynamically empty authorization list can accept a proof containing no valid signatures. [2](#0-1)  This creates a forged-empty-authorization condition analogous to a zero-beneficiary distribution succeeding while consuming the protected action. [5](#0-4) 

### Likelihood Explanation
The attack requires the verifier to call `verify` with an empty `keys_and_challenges` list. [4](#0-3)  This is most likely where the signature set or authorization list is dynamically derived and can become empty, while the malformed aggregate is fully attacker-controlled through `SchnorrAggregate::read`. [2](#0-1) 

### Recommendation
Reject zero constituent signatures in `SchnorrAggregate::verify` before checking equality with `keys_and_challenges`, matching the nonempty invariant enforced by `SchnorrAggregator::complete`. [4](#0-3) [1](#0-0) 

### Proof of Concept
```rust
use dalek_ff_group::Ristretto;
use schnorr::aggregate::SchnorrAggregate;

let mut encoded = vec![];
encoded.extend(0u32.to_le_bytes()); // Rs.len() == 0
encoded.extend([0u8; 32]);           // canonical scalar s == 0

let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut encoded.as_slice()).unwrap();

assert!(aggregate.verify(b"example aggregation domain", &[]));
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
