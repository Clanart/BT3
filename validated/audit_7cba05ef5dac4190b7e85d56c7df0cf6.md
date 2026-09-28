### Title
`SchnorrAggregate::verify` returns `true` for an empty aggregate (`Rs` empty, `s = 0`) instead of rejecting the invalid input - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
Analogous to `getAddresses()` returning zeroed-but-valid-looking results for an unrecognized key, `SchnorrAggregate::verify` treats an aggregate signature over **zero public keys** — the cryptographic equivalent of an empty/`0x0` lookup — as valid. `SchnorrAggregate::read` accepts a length prefix of `0` and a canonical scalar `s = 0` from untrusted bytes. `verify` then checks `self.Rs.len() != keys_and_challenges.len()` (`0 == 0` passes), queues no `(key, challenge)` pairs, and ends with the single pair `(-s, generator)` = `(0, G)`, whose multiexp is the identity — so it returns `true`.

Relevant code (`crypto/schnorr/src/aggregate.rs`):
- `read` accepts `len = 0` and `s = 0` (lines 77–88).
- `verify` returns `true` for the empty case (lines 127–146):

```rust
if self.Rs.len() != keys_and_challenges.len() {
  return false;
}
...
pairs.push((-self.s, C::generator()));
multiexp_vartime(&pairs).is_identity().into()
```

### Finding Description
`SchnorrAggregate` is the half-aggregation signature from eprint 2021/350. Verification is only sound when the aggregate is non-empty: with an empty signer set the verification equation degenerates to `0·G == identity`, which any byte string `0x0000…` length prefix plus a zero scalar satisfies. There is no check that `Rs`/`keys_and_challenges` is non-empty, and no check that `s` or the `Rs` are non-trivial — the function reports success on input carrying no cryptographic content at all, exactly as `getAddresses()` reports success on a token address carrying no DAO registration.

### Impact Explanation
Any downstream consumer that uses `SchnorrAggregate::verify` as a source of truth — e.g., an aggregator verifying a batch of participant signatures — can be fed the empty aggregate `read`-produced bytes (`u32::MAX`-free, just `00 00 00 00` followed by a zero scalar) and conclude verification succeeded for a set that contains no signers. This is a forged signature acceptance: an unprivileged party supplies untrusted bytes to `SchnorrAggregate::read` and the resulting object passes `verify`, which the stated rules classify as an acceptable outcome ("a forged proof or signature").

### Likelihood Explanation
Reachability requires a caller whose `keys_and_challenges` set can be empty while still invoking `verify` on attacker-controlled bytes — e.g., a protocol path where the set of expected signers is itself derived from untrusted input or can be empty under edge conditions. The `read`/`verify` API boundary itself does not defend against this; whether a specific in-scope caller exposes an empty-verification path could not be fully confirmed within the available searches (the main call sites live in processor/coordinator code outside the listed crypto scope). Rated Medium: the degenerate acceptance is real and reachable from public bytes, but end-to-end impact depends on integrator usage.

### Recommendation
Reject empty aggregates in `verify` (`if self.Rs.is_empty() { return false; }`), and/or reject `s == 0`/`Rs.is_empty()` in `SchnorrAggregate::read` so that a deserialized aggregate is never a vacuous pass. This mirrors the report's recommendation to error when the lookup key has no registered entry rather than returning default values.

### Proof of Concept
```rust
// bytes: len = 0, s = 0
let mut bytes = vec![0u8; 4];
bytes.extend(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref());
let sig = SchnorrAggregate::<Ristretto>::read(&mut bytes.as_slice()).unwrap();
assert!(sig.verify(b"any-dst", &[])); // returns true — no signature was verified
```
`multiexp_vartime(&[(-0, G)])` is the identity, so `is_identity()` yields `true`.