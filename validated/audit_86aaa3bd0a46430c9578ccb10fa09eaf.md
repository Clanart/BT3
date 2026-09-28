### Title
`ThresholdKeys::read` accepts a secret share inconsistent with the verification shares, producing an honest validator that emits invalid shares and is blamed/slashed, and whose group key does not match its secret - (File: crypto/dkg/src/lib.rs)

### Summary
CVE-2019-3960 is "unrestricted upload of a dangerous file type": the application accepts an attacker-controlled blob and treats it as a trusted object. The Serai analog is `ThresholdKeys::read`, which deserializes a `(params, interpolation, secret_share, verification_shares)` tuple from raw bytes and reconstructs a `ThresholdKeys` via `ThresholdKeys::new` without ever verifying the semantic invariant that binds the object together: `C::generator() * secret_share == verification_shares[params.i()]`. The deserialization accepts a "dangerous type" — a syntactically valid but semantically corrupt key object — which is then used by FROST signing machines.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) parses the curve ID, `t`, `n`, `i`, the interpolation variant (reading `n` scalars for `Constant`), the `secret_share` scalar, and `n` verification-share points, then calls `ThresholdKeys::new`.

`ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) validates only structural properties: the count of verification shares equals `n`, participant indexes are `<= n`, and `Constant` interpolation is only used when `t == n`. It computes `group_key` as the interpolation of `verification_shares[1..=t]` and stores `secret_share` verbatim. **No check binds `secret_share` to `verification_shares[i]` or to the derived `group_key`.**

The codebase itself acknowledges this hole: `AlgorithmSignatureMachine::complete` in crypto/frost/src/sign.rs:491-494 comments that "the only known way to cause this, for valid parameters/algorithms, is to deserialize a semantically invalid FrostKeys" — i.e., the authors know `ThresholdKeys::read` can produce internally inconsistent keys, and the downstream behavior on that path is a `FrostError::InternalError`/misblame rather than a clean rejection at parse time.

Downstream flow with corrupted keys (`view()` at crypto/dkg/src/lib.rs:463-533, `complete()` at crypto/frost/src/sign.rs:447-495):
- The node's `secret_share` no longer corresponds to its `verification_share`. Every signature share it emits in `sign()` fails `verify_share`, so `complete()` returns `FrostError::InvalidShare(own_i)`. In the processor (`processor/src/signer.rs:602-615`, `batch_signer.rs:282-287`, `cosigner.rs`, `slash_report_signer.rs`), `InvalidShare(l)` maps to `ProcessorMessage::InvalidParticipant { participant: l }`, causing the honest holder of the corrupted blob to be reported and removed as faulty.
- Alternatively, `verification_shares` can be set so the derived `group_key` is a key the node does not actually control (no participant's secret interpolates to it). Funds addressed to that `group_key` are scanned and reported as received (`Scanner::scan_transaction`, networks/bitcoin/src/wallet/mod.rs:199-214) yet are never spendable by the threshold — "funds reported received that are not spendable".
- Note `read` uses `C::read_G` via `Ciphersuite`, so identity/zero verification shares are also accepted (only `Curve::read_G` at crypto/frost/src/curve/mod.rs:125-131 rejects identity), enabling e.g. a verification share of identity that makes `verify_share` statements degenerate.

### Impact Explanation
An attacker who can supply `ThresholdKeys` bytes (key recovery/backup import, promoted keys, or any path feeding untrusted bytes to `ThresholdKeys::read` — an in-scope reachability class) can either (a) cause an honest validator to emit shares that fail verification, making the validator itself the blamed "faulty" party subject to removal/slashing, or (b) produce keys whose `group_key` does not correspond to the held shares, so deposits to that key are booked by the scanner but unspendable. This is the Serai equivalent of accepting a dangerous uploaded object whose internal invariants were never validated.

### Likelihood Explanation
Medium. Exploitation requires attacker influence over serialized key material rather than just network messages, which is a narrower reachability surface than pure signing-round inputs; however the missing check is unconditional, the parsing path is fully attacker-controlled once bytes reach `read`, and the code explicitly documents the semantically-invalid-keys failure mode. Effort is trivial once the byte sink is reachable; damage (wrongful blame/slash or unspendable reported funds) is concrete.

### Recommendation
In `ThresholdKeys::new`, after validating parameters, verify `C::generator() * secret_share == verification_shares[&params.i()]`, reject identity/zero verification shares (use the `Curve::read_G`-style identity check or check in `new`), and error out at deserialization time rather than deferring to the signing protocol's blame path.

### Proof of Concept
```rust
// Crafted blob: n=2, t=2 (Constant) or Lagrange; choose:
//   verification_shares[1] = G * 7  (attacker-chosen, e.g. a key attacker controls)
//   verification_shares[2] = G * 9
//   secret_share (for participant i=1) = 5   // inconsistent: G*5 != verification_shares[1]
// All scalars/points are canonical, so ThresholdKeys::<C>::read succeeds.
// Result: group_key() = G*7 + G*9 (from shares 1..=t); our interpolated share is 5.
// In FROST sign(): our share s_i satisfies nothing w.r.t. verification_shares[1],
// complete() fails signature verification, batch blame flags InvalidShare(Participant(1)),
// and the processor reports the honest node as a faulty participant.
```