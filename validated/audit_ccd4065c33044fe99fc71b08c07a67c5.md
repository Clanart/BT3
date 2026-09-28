### Title
`SchnorrAggregate::verify` accepts an empty aggregate (zero `Rs`, `s = 0`) — the degenerate path bypasses all per-signature checks - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
Analogous to GMX's `SwapUtils.swap()` skipping `minOutputAmount` when `swapPathMarkets.length == 0`, `SchnorrAggregate::verify` performs no rejection of the empty case. When `self.Rs` and `keys_and_challenges` are both empty, the equality check passes trivially and the multiexp reduces to a single `(-s, G)` pair. With `s = 0`, the result is the identity and verification returns `true` — a "signature" that attests to nothing is accepted as valid.

### Finding Description
In `SchnorrAggregate::verify` (crypto/schnorr/src/aggregate.rs:127-146), the only guard is `self.Rs.len() != keys_and_challenges.len()`. For the empty case:

- `pairs` contains only `(-self.s, C::generator())` (line 144).
- With `s = 0`, `multiexp_vartime(&[(0, G)])` yields the identity, so `verify` returns `true`.

Crucially, `SchnorrAggregate::read` (lines 77-88) happily deserializes a length-0 aggregate from untrusted bytes: a 4-byte little-endian zero length followed by a canonical zero scalar (36 bytes total for a 32-byte field) is a fully "valid" encoding. The producer side is protected — `SchnorrAggregator::complete` returns `None` when `self.sigs.is_empty()` (lines 175-178) — but the verifier does not mirror that invariant, so attacker-controlled bytes fed to `SchnorrAggregate::read` then `verify` forge acceptance. Any caller deriving `keys_and_challenges` from an attacker-influenced (empty) signer/message list — e.g. the `verify_aggregate` pattern of zipping `signers` with `aggregate.Rs()` — will accept the empty aggregate for the empty set, certifying that "the set signed" when nobody did.

### Impact Explanation
A forged aggregate signature is accepted by the verifier. Wherever the signer list can be empty (empty validator set for a round, an eventuality with no completions, an empty payment/batch set), an unprivileged party supplies ~36 bytes via `SchnorrAggregate::read` and passes `verify`, producing a proof of authorization that was never signed. This is an incorrect-verifier acceptance reachable purely from public inputs.

### Likelihood Explanation
Reachability requires a caller to invoke `verify` with an empty `keys_and_challenges` list and to trust the result. That is a degenerate-but-plausible state (a session/round with zero participants), and the attacker controls only public bytes, matching the reachable-input constraint. Since the honest aggregation path can never produce an empty aggregate (`complete` returns `None`), accepting one is unambiguously wrong — Medium severity: requires a degenerate caller state, but yields unconditional forgery once reached.

### Recommendation
Reject the empty case at the top of `SchnorrAggregate::verify`, mirroring the aggregator's invariant:

```rust
pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
  if self.Rs.is_empty() || (self.Rs.len() != keys_and_challenges.len()) {
    return false;
  }
  ...
}
```

Optionally also reject `len == 0` in `SchnorrAggregate::read` so the unverifiable encoding is never constructed.

### Proof of Concept
```rust
// crypto/schnorr: forge an aggregate "signature" over an empty signer set
let mut bytes = vec![0u8; 4];           // len = 0
bytes.extend(C::F::ZERO.to_repr());     // s = 0
let agg = SchnorrAggregate::<C>::read(&mut bytes.as_slice()).unwrap();
assert!(agg.Rs().is_empty());
assert!(agg.verify(b"any-dst", &[]));   // true — forged acceptance
```
`multiexp_vartime(&[(-0, G)]).is_identity()` holds, so `verify` returns `true` despite no signer existing.