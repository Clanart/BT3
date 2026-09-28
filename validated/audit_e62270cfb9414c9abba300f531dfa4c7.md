### Title
ThresholdKeys::read accepts identity verification shares, permitting a fully attacker-controlled group key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes the per-participant verification shares via `<C as Ciphersuite>::read_G`, which enforces canonical encoding but does **not** reject the identity point. This is in contrast to `Curve::read_G` in `crypto/frost/src/curve/mod.rs`, which explicitly rejects identity points for exactly this reason. The missing "identity blacklist" on the trusted key-loading path mirrors the advisory's bug class: a deserialization routine accepts a dangerous-but-well-formed input class that the protocol assumes was excluded, yielding a key whose security properties are silently void.

### Finding Description
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632), each of the `n` verification shares is read with `<C as Ciphersuite>::read_G(reader)` at line 622:

```rust
let mut verification_shares = HashMap::new();
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
```

`Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) checks only that the point decompresses and re-encodes canonically. The identity point is a valid canonical encoding, so it is accepted. The dedicated FROST reader `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131) exists specifically to reject identity — but `ThresholdKeys::read` does not use it, because `ThresholdKeys` is generic over `Ciphersuite`, not `Curve`.

`ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) then computes the group key as a Lagrange/Constant combination of `verification_shares[1 ..= t]` with no consistency check against `secret_share` and no identity rejection:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

A crafted blob with `verification_shares[i] = identity` for the first `t` participants deserializes successfully and produces `group_key = identity`, whose discrete log is publicly known (`0`). More generally, since neither the secret share nor the verification shares are checked for mutual consistency (`verification_shares[i] == G * share_i`) or non-triviality, the caller can be made to operate a "threshold" key whose group key is entirely attacker-determined. All downstream consumers (`view`, `group_key`, FROST `SignMachine`/`SignatureMachine`, `tweak_keys`, `Scanner`) trust `core.group_key` and `core.verification_shares` as the root of the key's security.

### Impact Explanation
Any deployment that loads a `ThresholdKeys` blob influenced by an untrusted party (key-package import, resharing handoff, backup/restore delivered over an authenticated-but-untrusted channel) can be handed a key whose group public key is the identity, or any other point with a discrete log known to the attacker. Consequences:

- Signatures verifying under `group_key` can be forged by anyone without any participant's cooperation: for `group_key = identity`, a Schnorr signature `(R = rG, s = r)` satisfies `R + c·A − sG = rG + 0 − rG = 0` for every challenge `c`.
- Bitcoin outputs received to `tweak_keys`'d / `p2tr_script_buf`'d versions of that key are spendable by the attacker alone — funds the node believes are threshold-secured are trivially stealable.
- The node's own secret share is irrelevant; the threshold assumption is silently voided without any error, panic, or blame event.

### Likelihood Explanation
Exploitation requires an attacker to supply or influence the serialized `ThresholdKeys` consumed by a victim (the format embeds the curve ID, so cross-curve confusion is already defended). Within a single honest PedPoP/dealer flow this is not reachable, but the library exposes `serialize`/`read` specifically for persistence and transport, and the read path is exactly where untrusted bytes enter. The defect is deterministic — no probabilistic or multi-party conditions — and the resulting keys pass all internal validation (`ThresholdKeys::new`, `ThresholdParams::new`, interpolation checks all succeed).

### Recommendation
Reject dangerous values at the trust boundary, analogous to the advisory's filename blacklist:

- In `ThresholdKeys::read`, reject identity verification shares (and ideally identity-equivalent encodings) when constructing `verification_shares`.
- In `ThresholdKeys::new` (or `read`), verify `verification_shares[params.i()] == C::generator() * secret_share` so the loaded share is consistent with the declared shares, and consider recomputing/validating that the interpolated `group_key` is non-identity.
- Alternatively, have `Ciphersuite::read_G`-based key material deserialization share the identity-rejecting semantics of `Curve::read_G`, since no legitimate threshold key has identity shares.

### Proof of Concept
For `t = n = 1`, participant `i = 1`, `Interpolation::Lagrange` (tag byte `1`), `secret_share = 0`, `verification_shares = {1: identity}`:

1. Serialize: `C::ID.len()` (u32 LE) ‖ `C::ID` ‖ `t=1` ‖ `n=1` ‖ `i=1` ‖ `0x01` ‖ `F::ZERO.to_repr()` ‖ `G::identity().to_bytes()`.
2. `ThresholdKeys::<C>::read` succeeds; `keys.group_key() == C::G::identity()` since the sum over `{1}` is `identity * 1`.
3. Any Schnorr-verifiable consumer of `keys.group_key()` accepts attacker-forged signatures `(rG, r)`; `keys.view(vec![1])` yields `secret_share = 0` and `verification_shares[1] = identity`, and FROST `sign`/`complete` will happily produce shares consistent with the identity key — all while the group key's "private key" (`0`) is public knowledge.

Root cause: crypto/dkg/src/lib.rs:620-623 (`read_G` without identity rejection) combined with crypto/dkg/src/lib.rs:376-378 (group key derived from unvalidated shares).