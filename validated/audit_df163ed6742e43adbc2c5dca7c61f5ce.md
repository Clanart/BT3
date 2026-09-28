### Title
`SchnorrAggregate::verify` accepts a forged aggregate signature over an empty signer set - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` returns `true` when `keys_and_challenges` is empty and the attacker-supplied scalar `s` is zero. The verification equation reduces to `-0 * G == identity`, which trivially holds. This is the same bug class as the referenced advisory: a signature object is accepted as valid without any underlying key having ever signed anything. Any verifier which consumes attacker-controlled bytes via `SchnorrAggregate::read` and pairs them with an attacker-influenced (or empty) signer list accepts a forged aggregate signature.

### Finding Description
`SchnorrAggregate` is `Rs: Vec<C::G>` plus scalar `s`, both fully attacker-controlled via `SchnorrAggregate::read`, which reads a `u32` length, that many `read_G` points, and a `read_F` scalar.

In `verify`:

```rust
// crypto/schnorr/src/aggregate.rs
if self.Rs.len() != keys_and_challenges.len() {
  return false;
}
...
let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
  let z = weight(&mut digest);
  pairs.push((z, self.Rs[i]));
  pairs.push((z * challenge, *key));
}
pairs.push((-self.s, C::generator()));
multiexp_vartime(&pairs).is_identity().into()
```

When `keys_and_challenges` is empty and `Rs` is empty, the only pair pushed is `(-self.s, C::generator())`. Setting `s = 0` makes `multiexp_vartime` return the identity, so `verify` returns `true`. The function never requires at least one signer, never requires the claimed public keys to be members of a known set itself (it trusts the caller-supplied list), and produces "valid" for a signature no key ever produced.

This mirrors a real consumer pattern: `Validators::verify_aggregate` in `coordinator/tributary/src/tendermint/mod.rs` takes the `signers` list and the serialized aggregate from an incoming `Commit` — both derived from the peer's message — and calls `aggregate.verify(DST, ...)` after only checking `signers.len() == aggregate.Rs().len()`. A commit carrying `validators = []` and `signature = <len=0, s=0>` passes this cryptographic check; acceptance then rests entirely on whatever quorum/weight check exists upstream, which the signature layer does not enforce or document.

### Impact Explanation
A verifier formula that yields `true` for a signature produced by no key is a forgery primitive. The half-aggregation scheme (eprint 2021/350) assumes a non-empty signer set; nothing in `read` or `verify` enforces it. The concrete blast radius in Serai is bounded by whether callers separately enforce quorum membership of `signers`, but the primitive itself — "does this aggregate prove these keys signed" — answers "yes" for zero keys, which is incorrect for every caller that treats the length check + `verify` as sufficient.

### Likelihood Explanation
Reachable from untrusted input: `SchnorrAggregate::read` accepts a 4-byte `0x00000000` length followed by a zero scalar — 36 bytes total. Whether it yields a security failure depends on the caller passing an empty `signers`/`keys_and_challenges` list; where that list comes from signed-message data (e.g. `commit.validators`), an attacker controls both sides. Likelihood is conditional on a caller allowing an empty list to reach `verify`, which is plausible for any code path that validates the count match only after reading, as in `verify_aggregate`.

### Recommendation
Reject `self.Rs.is_empty()` (or `keys_and_challenges.is_empty()`) in `SchnorrAggregate::verify`, matching `SchnorrAggregator::complete` which already refuses to produce an empty aggregate (`if self.sigs.is_empty() { return None; }`). Document that `verify` is only meaningful over a caller-authenticated, non-empty signer set.

### Proof of Concept
```rust
// Any Ciphersuite C
let mut bytes = vec![];
bytes.extend(0u32.to_le_bytes());           // zero Rs
bytes.extend(C::F::ZERO.to_repr().as_ref()); // s = 0

let agg = SchnorrAggregate::<C>::read::<&[u8]>(&mut bytes.as_slice()).unwrap();
assert!(agg.verify(b"any dst", &[])); // forges a valid aggregate over no signers
```
The multiexp contains only `(-0) * G`, which is the identity, so verification succeeds despite no key having signed.