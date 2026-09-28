### Title
Empty Schnorr aggregate deserializes and verifies as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts a zero-length `Rs` vector, while `SchnorrAggregate::verify` accepts it when the caller supplies an empty `keys_and_challenges` list. This creates a valid verification result for a signature aggregate that `SchnorrAggregator::complete` explicitly refuses to produce.

### Finding Description
The deserializer reads a 32-bit count and then consumes that many points followed by one scalar, without rejecting `count == 0`. Verification only checks that the number of nonces equals the number of supplied keys/challenges. For an empty aggregate and empty key list, the sole remaining statement is `(-s) * G`; choosing the canonical scalar `s = 0` makes that statement the identity and verification succeeds.

### Impact Explanation
A caller that relies on successful aggregate verification as evidence that at least one signer participated can accept a forged “aggregate signature” containing no signer nonce and no signer contribution. The invalid object is reachable entirely from public bytes passed to `SchnorrAggregate::read` and public verification inputs.

### Likelihood Explanation
The malicious encoding is only 36 bytes: a zero nonce count followed by a zero scalar. Exploitation requires the surrounding protocol to invoke aggregate verification with an empty signer/challenge list; the library itself does not enforce the invariant that an aggregate must contain at least one signature.

### Recommendation
Reject `len == 0` in `SchnorrAggregate::read`, and defensively return `false` from `SchnorrAggregate::verify` when `keys_and_challenges` is empty. This matches `SchnorrAggregator::complete`, which returns `None` when no signatures were aggregated.

### Proof of Concept
`crypto/schnorr/src/aggregate.rs` accepts attacker-controlled nonce count and scalar bytes:

```rust
// 4-byte little-endian nonce count = 0, followed by a canonical zero scalar.
let malicious = [0u8; 36];
let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut malicious.as_slice()).unwrap();

// Succeeds because both lists are empty and (-0) * G is identity.
assert!(aggregate.verify(b"example-dst", &[]));
```

The vulnerable paths are:
- `SchnorrAggregate::read` permits zero `Rs`: `crypto/schnorr/src/aggregate.rs:77-87`.
- `SchnorrAggregate::verify` checks only length equality before evaluating `(-s) * G`: `crypto/schnorr/src/aggregate.rs:127-145`.
- Honest aggregation rejects an empty aggregate: `crypto/schnorr/src/aggregate.rs:175-178`.