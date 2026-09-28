### Title
Empty aggregate signature verifies without any signer - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
`SchnorrAggregate::read` accepts an aggregate containing zero nonces, and `SchnorrAggregate::verify` accepts it when `s` is zero. Because the verification equation reduces to `0 * G == 0`, a completely unsigned aggregate verifies for an empty `keys_and_challenges` list.

### Finding Description
`SchnorrAggregate::read` reads the encoded `Rs` length and returns an aggregate without rejecting `len == 0`. It then accepts a canonical zero scalar as `s`.

`SchnorrAggregate::verify` only checks that the number of nonces equals the number of `(key, challenge)` pairs. If both are empty, it queues no per-signature terms, queues only `(-s)G`, and accepts when `s == 0`.

Local aggregation cannot produce this state because `SchnorrAggregator::complete` returns `None` when no signatures were aggregated. The vulnerable path is therefore specifically deserialization of attacker-controlled aggregate bytes followed by verification.

### Impact Explanation
An unprivileged party can forge a cryptographically valid aggregate signature without controlling any key or obtaining any individual signature. Any caller that treats `SchnorrAggregate::verify` as proof that at least one listed signer authorized an action can accept an aggregate signed by nobody.

### Likelihood Explanation
Exploitation requires the surrounding verifier to permit an empty signer list or to omit a separate quorum check after aggregate verification. The cryptographic API itself does not reject that state, so the impact is limited to integrations which treat aggregate verification as sufficient authorization.

### Recommendation
Reject empty aggregates in both `SchnorrAggregate::read` and `SchnorrAggregate::verify`. At minimum, `verify` should return `false` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()`, and `read` should reject an encoded nonce count of zero for consistency with `SchnorrAggregator::complete`.

### Proof of Concept
```rust
// crypto/schnorr/src/aggregate.rs

let mut encoded = Vec::new();
encoded.extend(0u32.to_le_bytes()); // zero Rs
encoded.extend(C::F::ZERO.to_repr().as_ref()); // s = 0

let aggregate = SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();

// Accepted: there are no Rs and no keys/challenges, so the equation is
// (-0) * G == identity.
assert!(aggregate.verify(b"application domain separator", &[]));
```