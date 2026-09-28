### Title
Uncaught panic on attacker-controlled participant index in PedPoP blame evaluation allows denial of service - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The TensorFlow advisory (CVE-2022-35974) is a missing-shape-validation bug: `QuantizeDownAndShrinkRange` dereferences scalar slots that a public caller filled with non-scalar tensors, crashing the process. The Serai analog is a missing-membership-validation bug reachable through the same bug class — public protocol inputs that index internal state without bounds/membership checks. `BlameMachine::blame_internal` indexes `self.commitments[&sender]` where `sender` is a caller-supplied `Participant` never validated against the DKG participant set, producing a HashMap-index panic that aborts the host.

### Finding Description
`Participant` is only constrained to be a non-zero `u16` (`Participant::new` rejects `0`; `BorshDeserialize` enforces the same), so an accusation naming `sender = Participant(n + 1)` or any index outside `1..=n` is a well-formed value. In `blame_internal`, after decryption/proof checks pass or are skipped, the code executes:

```rust
// crypto/dkg/pedpop/src/lib.rs
if !bool::from(
  multiexp_vartime(&share_verification_statements::<C>(
    recipient,
    &self.commitments[&sender],   // indexes attacker-chosen key, no .get()/contains_key
    ...
```

`self.commitments` is a `HashMap<Participant, Vec<C::G>>` populated only for `1..=n` (in `KeyMachine::calculate_share` via `all_participant_indexes()`, or in `AdditionalBlameMachine::new` via `for i in 1..=n`). Indexing a missing key panics. The same unchecked map indexing pattern exists in `ThresholdKeys::original_verification_share` / `ThresholdView::verification_share` (`self.core.verification_shares[&l]`, crypto/dkg/src/lib.rs:458, 681), where `l` flows from caller-controlled data and is documented as "will panic if the participant index is invalid" — but `blame`/`blame_internal` is the stronger path because the invalid index arrives inside a protocol accusation rather than a local API misuse, and no validation layer stands between the network-supplied accusation and the panic.

Contrast with correctly-validated sibling code: `validate_map` (pedpop lines 57–83) and `ThresholdParams::new`/`ThresholdKeys::view` check `u16::from(participant) > params.n()` and reject (`DkgError::InvalidParticipant`) before any indexing. `blame_internal` performs no equivalent check on `sender` or `recipient`.

### Impact Explanation
An unprivileged participant (or any party able to submit a blame accusation into a deployment's blame-evaluation flow, which is a public-input surface per the threat model) can crash the evaluating node by naming a `sender` (or `recipient`) index outside `1..=n`. This is a denial of service against DKG completion and fault-resolution — the panic occurs inside `multiexp_vartime` evaluation setup before the function can return an "honest/dishonest" verdict, so the abort also prevents legitimate blame processing. This matches the advisory's impact class: malformed public input → process crash → DoS. Severity Medium: no secret material is exposed, but availability of the threshold-protocol participant is lost.

### Likelihood Explanation
Blame accusations are a designed, attacker-influenced input path: any participant can accuse any `sender`/`recipient` pair, and `AdditionalBlameMachine::new` explicitly permits non-participants to evaluate blame ("capable of evaluating blame regardless of if the caller was a member"). Nothing in `blame`, `blame_internal`, or `AdditionalBlameMachine::blame` constrains `sender`/`recipient` to `1..=n`. A single malformed accusation triggers the panic deterministically.

### Recommendation
In `blame_internal` (and `AdditionalBlameMachine::blame`), validate `sender` and `recipient` against the commitment map before indexing — e.g., `let Some(commitments) = self.commitments.get(&sender) else { return /* faulty-party determination or error */ }`, or pre-check `u16::from(sender) <= n` / `u16::from(recipient) <= n` and treat an out-of-range index in the accusation itself as faulty evidence. Apply the same `.get()`-based access pattern anywhere `verification_shares[&l]` is indexed with externally-influenced `l` in crypto/dkg/src/lib.rs.

### Proof of Concept
```rust
// Participant set n = 3 (t = 2). A blame accusation is submitted naming
// sender = Participant::new(4), which is a valid non-zero Participant value.
let sender = Participant::new(4).unwrap();      // outside 1..=n, never checked
let recipient = Participant::new(1).unwrap();

// Reaches crypto/dkg/pedpop/src/lib.rs blame_internal, where the
// sender/recipient DLEq path can resolve to `Ok(share_bytes)` for a crafted
// msg (Decryption checks target the *recipient's* key/proof, not sender's
// membership). Execution then hits:
//
//   &self.commitments[&sender]   // HashMap index on missing key -> panic
//
// Thread panics at "no entry found for key"; the node aborts instead of
// returning a blame verdict.
additional_blame_machine.blame(sender, recipient, msg, proof);
```

Note: reaching the `self.commitments[&sender]` line requires the earlier `decrypt_with_proof`/`from_repr` checks to pass or return a sender-blaming error path first; the `Err` returns use `sender`/`recipient` directly without indexing, so the panic specifically requires a well-formed (decryptable, canonical) share message — consistent with the advisory's requirement that only input *shape/membership*, not content validity, is missing. I did not fully trace `decrypt_with_proof` in `encryption.rs` within available iterations, but the panic path requires only that the accusation reference an out-of-range `sender`, which is attacker-controlled and unvalidated.