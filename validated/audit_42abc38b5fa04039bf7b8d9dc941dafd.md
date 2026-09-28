### Title
Zero-Length Schnorr Aggregate Always Verifies - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero nonces when the supplied key/challenge list is also empty. An attacker can deserialize `Rs = []` and `s = 0`; verification then performs no per-signature checks and evaluates only `0 * G`, which is always identity. [1](#0-0) [2](#0-1) 

### Finding Description
`SchnorrAggregate::read` accepts a serialized count of zero and returns an aggregate with an empty `Rs` vector. [1](#0-0)  `verify` only requires `self.Rs.len() == keys_and_challenges.len()`, so empty attacker-controlled bytes satisfy the length check when the verifier's list is empty. [3](#0-2)  The verification loop queues no signature terms, then queues `(-0) * G`, making the multiexp result the identity for any domain separator. [4](#0-3) 

This contradicts the constructor's own invariant: `SchnorrAggregator::complete` explicitly returns `None` when no signatures were aggregated. [5](#0-4) 

### Impact Explanation
This permits a forged aggregate Schnorr signature for the degenerate zero-signer statement. Any caller which treats `verify(dst, keys_and_challenges)` as authorization without independently enforcing a non-empty signer set will accept attacker-supplied bytes that prove control of no key. [2](#0-1) 

### Likelihood Explanation
The malicious input is only the four-byte count `0` followed by a canonical zero scalar, so it is trivial to construct. Exploitation requires a reachable verification path where the signer/challenge list can be empty; within that path, success is deterministic rather than probabilistic. [1](#0-0) [2](#0-1) 

### Recommendation
Reject empty aggregates in `SchnorrAggregate::verify` by returning `false` when `self.Rs.is_empty()`, before checking `keys_and_challenges.len()`. Optionally also reject zero-count aggregates in `SchnorrAggregate::read`, matching `SchnorrAggregator::complete`'s refusal to create empty aggregates. [2](#0-1) [5](#0-4) 

### Proof of Concept
For any `C: Ciphersuite`, serialize `Rs.len() = 0` followed by `C::F::ZERO`:

```rust
let mut bytes = vec![];
bytes.extend(0u32.to_le_bytes());          // zero Rs
bytes.extend(C::F::ZERO.to_repr().as_ref()); // s = 0

let aggregate = SchnorrAggregate::<C>::read(&mut bytes.as_slice()).unwrap();
assert!(aggregate.verify(b"example-domain-separator", &[]));
```

Verification succeeds because the per-signature loop is empty and the final equation is `0 * G == identity`. [1](#0-0) [4](#0-3)

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

**File:** crypto/schnorr/src/aggregate.rs (L174-180)
```rust
  /// Complete aggregation, returning None if none were aggregated.
  pub fn complete(mut self) -> Option<SchnorrAggregate<C>> {
    if self.sigs.is_empty() {
      return None;
    }

    let mut aggregate = SchnorrAggregate { Rs: Vec::with_capacity(self.sigs.len()), s: C::F::ZERO };
```
