### Title
Empty `SchnorrAggregate` bypasses signature verification - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero signatures and a scalar `s = 0`. Although `SchnorrAggregator::complete` explicitly refuses to produce an empty aggregate, the public deserializer accepts one. Verification then reduces to checking whether `0 * G` is the identity, which always succeeds. This is analogous to the reported pair invariant degenerating to zero: an empty verification equation is treated as valid instead of being rejected.

### Finding Description
`SchnorrAggregate::read` reads a `u32` nonce-count and permits that count to be zero, then reads a canonical scalar `s` at `crypto/schnorr/src/aggregate.rs:77-87`.

`SchnorrAggregate::verify` only checks that `self.Rs.len() == keys_and_challenges.len()` at `crypto/schnorr/src/aggregate.rs:127-130`. If both are empty:

1. The challenge loop appends nothing at `crypto/schnorr/src/aggregate.rs:132-136`.
2. The statement loop adds no public-key or nonce terms at `crypto/schnorr/src/aggregate.rs:138-143`.
3. The only queued statement is `(-s) * G` at `crypto/schnorr/src/aggregate.rs:144`.
4. For attacker-supplied `s = 0`, `multiexp_vartime(&[(0, G)])` is the identity, so verification returns `true` at `crypto/schnorr/src/aggregate.rs:145`.

This contradicts the aggregator’s own semantics: `SchnorrAggregator::complete` returns `None` when no signatures were aggregated at `crypto/schnorr/src/aggregate.rs:174-178`, establishing that an empty aggregate is not a valid aggregate signature.

### Impact Explanation
An unauthenticated party can provide a serialized aggregate signature with zero nonce commitments and a zero scalar. Any caller that deserializes it with `SchnorrAggregate::read` and calls `verify` with an empty `keys_and_challenges` slice receives `true`, despite no private key holder having signed anything.

This is an incorrect verifier formula caused by a degenerate zero-sized statement. The aggregate verifier should verify at least one Schnorr signature; instead, the empty conjunction is accepted as satisfying the signature equation.

### Likelihood Explanation
The forged object is trivially constructible from public inputs:

- four little-endian zero bytes for `Rs.len() == 0`;
- the canonical zero scalar encoding for `s = 0`.

No private key, nonce, valid signature, or protocol participant cooperation is required. Exploitability depends on an application accepting an empty aggregate verification context, but the cryptographic library itself creates the unsafe acceptance behavior rather than rejecting the malformed aggregate during parsing or verification.

### Recommendation
Reject empty aggregates consistently in both parsing and verification:

```rust
pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
  if self.Rs.is_empty() || (self.Rs.len() != keys_and_challenges.len()) {
    return false;
  }

  // existing verification logic
}
```

`SchnorrAggregate::read` should also reject a zero length so malformed wire objects cannot enter downstream verification paths. This aligns the deserializer and verifier with `SchnorrAggregator::complete`, which already refuses to emit an empty aggregate.

### Proof of Concept
For a ciphersuite whose scalar uses a 32-byte canonical encoding, such as Ristretto255, the forged payload is:

```rust
let mut serialized = Vec::new();
serialized.extend_from_slice(&0u32.to_le_bytes()); // Rs.len() == 0
serialized.extend_from_slice(&[0u8; 32]);          // s == 0

let aggregate =
  SchnorrAggregate::<Ristretto>::read(&mut serialized.as_slice()).unwrap();

assert!(aggregate.verify(b"application domain", &[]));
```

The deserialized object contains no nonce commitments and no proof of scalar multiplication by any signer key. Nevertheless, the statement list degenerates to only `-0 * G`, which evaluates to the identity and makes `verify` return `true`.