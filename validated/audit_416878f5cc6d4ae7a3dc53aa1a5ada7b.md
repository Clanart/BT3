### Title
`SchnorrAggregate::verify` accepts a forged signature over an empty signer set — the verification transcript is never initialized with any statement — (File: crypto/schnorr/src/aggregate.rs)

### Summary
The bug class in the external report is a security check that is vacuous because a required initialization step was never performed (owner never set, so `onlyOwner` is meaningless). The analog in Serai is `SchnorrAggregate::verify` in `crypto/schnorr/src/aggregate.rs`: the weight-derivation transcript and the multiexp are never "initialized" with any constraint when `keys_and_challenges` is empty, so the verification equation degenerates to `-s·G == identity`. Combined with `SchnorrAggregate::read` — which accepts a zero-length `Rs` vector and a zero `s` from untrusted bytes — any party can serialize a "signature" (`Rs = []`, `s = 0`) that `verify` returns `true` for, with zero public keys bound to it. The aggregator side refuses this case (`SchnorrAggregator::complete` returns `None` when empty), but the verifier side has no matching check.

### Finding Description
`SchnorrAggregate::read` reads a `u32` count, that many points into `Rs`, and a scalar `s`, with no check that the count is non-zero or that `s` is non-zero (`crypto/schnorr/src/aggregate.rs`, `SchnorrAggregate::read`). `verify` then:

- builds a `DigestTranscript` and appends only the caller-supplied challenges (none, when the signer set is empty);
- pushes `(z_i, R_i)` and `(z_i·c_i, P_i)` pairs per signer (none);
- pushes `(-s, generator)` and requires the multiexp to be identity.

With `Rs = []`, `keys_and_challenges = []`, `s = 0`, the only pair is `(0, G)` and `multiexp_vartime` returns the identity point, so `verify` returns `true`. The check is vacuous for exactly the same reason as the uninitialized `onlyOwner`: the value that was supposed to constrain the operation (the accumulated statements/weights) was never populated, so the "verification" trivially passes.

This is reachable from public inputs: `SchnorrAggregate::read` consumes raw untrusted bytes, and `verify` is a public API. A concrete caller is `Validators::verify_aggregate` in `coordinator/tributary/src/tendermint/mod.rs`, which reads an attacker-controlled `AggregateSignature`, checks `signers.len() != aggregate.Rs().len()` (0 == 0 passes), derives zero challenges, and calls `aggregate.verify(DST, &[])` — returning `true` for a byte string the attacker fabricated.

### Impact Explanation
A public `verify` API returns `true` on attacker-supplied bytes for which no signer ever produced a signature. That is a forged-signature acceptance in the verifier itself: `SchnorrAggregate { Rs: [], s: 0 }` verifies against any DST and any message (the message never enters `verify` except through caller-supplied challenges, of which there are none). Any downstream code that treats `verify`/`verify_aggregate` returning `true` as "this aggregate signature is authentic" without independently enforcing a non-empty signer set accepts a forgery. `verify_aggregate` in the Tendermint signature scheme performs no such check beyond the length equality, which the forged object satisfies trivially.

### Likelihood Explanation
Exploitation requires a caller that invokes `verify`/`verify_aggregate` with an attacker-influenced or empty signer list and acts on the boolean result. The forged encoding is 36 constant bytes (`0x00000000` length prefix plus a zero scalar), requiring no key knowledge, no interaction, and no privileged position — anyone who can feed bytes to `SchnorrAggregate::read` and cause `verify` to be evaluated against an empty `keys_and_challenges` triggers it. The asymmetry between `SchnorrAggregator::complete` (rejects empty) and `SchnorrAggregate::verify` (accepts empty) means the invariant "a verifying aggregate was produced by real aggregation" is not enforced anywhere in the crate.

### Recommendation
Mirror the aggregator's invariant in the verifier: in `SchnorrAggregate::verify`, return `false` when `self.Rs.is_empty()` (equivalently when `keys_and_challenges.is_empty()` after the length check). Defense in depth: in `SchnorrAggregate::read`, reject a zero `Rs` length, and in `Validators::verify_aggregate` (`coordinator/tributary/src/tendermint/mod.rs`), reject an empty `signers` slice before verification so a zero-weight signer list can never produce `true`.

### Proof of Concept
```rust
// crypto/schnorr — forged aggregate accepted by verify()
// Encoding: u32 LE count = 0, then a zero scalar s.
let mut forged = Vec::new();
forged.extend(0u32.to_le_bytes());          // Rs.len() == 0
forged.extend(<C as Ciphersuite>::F::ZERO.to_repr().as_ref()); // s == 0

let agg = SchnorrAggregate::<C>::read::<&[u8]>(&mut forged.as_ref()).unwrap();
assert_eq!(agg.Rs().len(), 0);

// No signers, no challenges: the only multiexp pair is (0, G) -> identity.
assert!(agg.verify(b"any DST", &[]));
```

In the Tendermint caller (`Validators::verify_aggregate`), `signers = &[]` yields `signers.len() == aggregate.Rs().len() == 0`, an empty `challenges` vector, and `aggregate.verify(DST, &[]) == true` — the forged 36-byte `AggregateSignature` verifies without any validator having signed.

Uncertainty: whether a reachable upstream path (e.g., Tendermint consensus) ever calls `verify_aggregate` with an attacker-selectable empty signer list depends on BFT weight accounting outside this code; the verifier-level forgery itself is unconditional.