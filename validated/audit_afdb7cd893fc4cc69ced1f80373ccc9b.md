### Title
`ThresholdKeys::read`/`ThresholdKeys::new` never validates that `secret_share` matches its verification share — deserialized keys can be internally inconsistent - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The bug class in the external report is "a consumer accepts returned data without a completeness/consistency check, so a stale or malformed result is silently used." In Serai, `ThresholdKeys::read` deserializes `secret_share` and the per-participant `verification_shares` map from untrusted bytes, and `ThresholdKeys::new` derives `group_key` solely from `verification_shares[1..=t]` — it never checks the completeness invariant `secret_share * G == verification_shares[i]`. The result is a `ThresholdKeys` whose group key is not the key the local secret share can actually sign for.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i`, the interpolation variant, a raw `secret_share`, and `n` verification shares, then calls `ThresholdKeys::new` ([crypto/dkg/src/lib.rs:574-632](crypto/dkg/src/lib.rs)). `ThresholdKeys::new` only validates the *count* and *index range* of `verification_shares` and computes `group_key` as the interpolated sum of shares `1..=t` ([crypto/dkg/src/lib.rs:355-378](crypto/dkg/src/lib.rs)):

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

No check anywhere binds the deserialized `secret_share` to `verification_shares[params.i()]`. `view()` then interpolates `secret_share` and the attacker-controlled shares independently ([crypto/dkg/src/lib.rs:494-521](crypto/dkg/src/lib.rs)), and `AlgorithmSignatureMachine::complete` produces a signature against `view.group_key()` ([crypto/frost/src/sign.rs:465](crypto/frost/src/sign.rs)).

### Impact Explanation
A crafted serialized `ThresholdKeys` yields a `group_key` whose discrete log the holder does not possess — the `secret_share` does not correspond to any interpolation of the verification shares. Any `ThresholdView` derived from it signs with a wrong effective share, so `params.algorithm.verify(view.group_key(), &Rs, sum)` fails in `complete`. The code then runs share-blame verification; all *interpolated* verification shares are self-consistent with the (inconsistent) secret share only for the attacker-chosen share, and honest participants' shares verify — hitting the `FrostError::InternalError("everyone had a valid share yet the signature was still invalid")` path. Funds addressed to the reported `group_key` are unspendable by this participant, and every signing attempt aborts. Analogous to the oracle issue: the accessor returns a value (group key / share set) whose completeness relative to the returned secret was never verified, so a stale/inconsistent result is consumed.

### Likelihood Explanation
`ThresholdKeys::read` is an explicitly untrusted-bytes entry point. Anyone who can supply or corrupt the serialized key material (backup/recovery import, DB row, peer-provided key package) can set `secret_share` arbitrarily while keeping well-formed verification shares; nothing rejects it. Deterministic, no cryptographic break required.

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * secret_share == verification_shares[&params.i()]` before constructing `ThresholdCore`, and return a `DkgError` on mismatch. Equivalently, add the check in `ThresholdKeys::read` so deserialization enforces the completeness invariant, matching the "check round completeness / `answeredInRound >= roundId`" recommendation of the source report.

### Proof of Concept
1. Serialize a valid `ThresholdKeys`, then overwrite the `secret_share` field with a different scalar (or write shares such that `verification_shares[i] != secret_share * G`).
2. Feed the bytes to `ThresholdKeys::read::<&[u8]>`. It returns `Ok` — no consistency check exists.
3. Call `.view(included)` and run `AlgorithmSignatureMachine::complete` with honest shares from other participants. `self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum)` fails ([crypto/frost/src/sign.rs:465](crypto/frost/src/sign.rs)), per-share blame passes for honest shares, and signing terminates in `FrostError::InternalError`, rendering the wallet under the reported `group_key` unspendable.