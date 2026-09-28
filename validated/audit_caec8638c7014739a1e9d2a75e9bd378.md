### Title
Unvalidated `sender`/`recipient` participant indexes panic in PedPoP blame evaluation, causing a repeatable crash (DoS) - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
`BlameMachine::blame_internal` and `AdditionalBlameMachine::blame` index `self.commitments[&sender]` directly with a `Participant` value taken verbatim from the blame request. `Participant` is only constrained to be a non-zero `u16` (`Participant::new` rejects 0, `crypto/dkg/src/lib.rs:29-35`), while `commitments` only ever contains the keys `1 ..= n`. Any accuser-supplied `sender` in `(n, u16::MAX]` — or any `sender` whose commitments were not registered — reaches a `HashMap` index on a missing key and panics. This maps the MySQL Optimizer crash/hang class (CVE-2026-61144) onto a reachable, unprivileged input: blame messages (`EncryptedMessage`, `EncryptionKeyProof`) and the accused/accuser indexes are attacker-controlled protocol inputs fed to `AdditionalBlameMachine::new`/`blame`.

### Finding Description
`AdditionalBlameMachine::new` populates `commitments` strictly for `1 ..= n` (`crypto/dkg/pedpop/src/lib.rs:656-660`). `blame` forwards `sender` and `recipient` to `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:674-682`), which ends with:

```rust
if !bool::from(
  multiexp_vartime(&share_verification_statements::<C>(
    recipient,
    &self.commitments[&sender],
    Zeroizing::new(share),
  ))
  ...
```

(`crypto/dkg/pedpop/src/lib.rs:595-604`). `self.commitments[&sender]` is an unchecked `HashMap` index — it panics on any absent key. Neither `blame`, `blame_internal`, nor `AdditionalBlameMachine::new` bounds `sender`/`recipient` against `n`; the doc comments only require uniqueness of blame statements, not index validity. The same unchecked indexing exists in `KeyMachine::calculate_share`'s blame path via `blames.remove(&l).unwrap()` (line 496) — that one is safe because `l` comes from a `validate_map`-checked set, but the blame API takes `sender`/`recipient` directly from the caller with no equivalent validation.

This is reachable downstream: `processor/src/key_gen.rs:504-556` reads an attacker-influenced `VerifyBlame` message, builds `AdditionalBlameMachine::new(context, params.n(), commitment_msgs)`, and calls `.blame(accuser, accused, share, proof)` where `accused` is a `Participant` field from the message. If `accused` exceeds `n`, the indexing panics — and the processor/relayer binaries install a panic hook that kills the process (cf. `networks/ethereum/relayer/src/main.rs:11-19`), converting the panic into a full node crash rather than a caught error.

### Impact Explanation
A single crafted blame request causes a panic that aborts the DKG fault-resolution flow and, under the panic-hook deployment, terminates the process — a repeatable, remotely triggerable crash (complete DoS of the signing/key-gen pipeline for that session). This is the direct analog of the advisory's "hang or frequently repeatable crash" availability impact.

### Likelihood Explanation
Medium-to-low: the attacker must be able to submit or trigger a blame evaluation (an accuser role or a message routed to `VerifyBlame`), and the panic only fires if the `msg`/`proof` path reaches the commitment lookup (i.e., `decrypt_with_proof` does not early-return). That ordering is not fully verified — if `decrypt_with_proof` internally rejects unknown senders first, the panic is unreachable via that specific ordering — but no bounds check on `sender`/`recipient` exists anywhere in `pedpop`, so any future or alternate call path (or a `sender` within `1..=n` whose commitments were dropped) hits the same unchecked index. CVSS-analogous: high-privilege-adjacent reachability, availability-only impact ≈ Medium.

### Recommendation
In `blame_internal` (and `AdditionalBlameMachine::new`'s callers), validate `sender` and `recipient` against `1 ..= n` and membership in `self.commitments` before indexing — replace `self.commitments[&sender]` with a `get` that returns `sender` as faulty (or an explicit error) on `None`. Mirror the `validate_map`/`InvalidParticipant` pattern already used elsewhere in the crate.

### Proof of Concept
1. Complete or simulate a PedPoP DKG to obtain a `BlameMachine`/`AdditionalBlameMachine` with `n = 3`.
2. Call `blame(Participant::new(4).unwrap(), Participant::new(1).unwrap(), msg, None)` where `msg` is any `EncryptedMessage` that survives `decrypt_with_proof` (or, on a build where the panic hook exits the process, submit a `VerifyBlame` with `accused = Participant(4)`).
3. `self.commitments[&sender]` at `crypto/dkg/pedpop/src/lib.rs:599` panics (`HashMap` index on missing key), crashing the evaluation — repeatable on every invocation.

Caveat: I could not fully read `crypto/dkg/pedpop/src/encryption.rs` to confirm whether `decrypt_with_proof` rejects an out-of-range `sender` before the commitments lookup; if it does, the panic requires a `sender` that is registered-looking but absent from `commitments`, or a caller that invokes `blame` with commitments for a different participant set — the missing bounds check remains a defect either way.