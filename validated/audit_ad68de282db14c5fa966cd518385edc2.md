### Title
Unprivileged participant crashes blame adjudication by supplying an out-of-set `Participant` index - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame_internal` (reached via `BlameMachine::blame` and `AdditionalBlameMachine::blame`) indexes `self.commitments` with the attacker-influenced `sender` argument using `HashMap` indexing (`self.commitments[&sender]`). `Participant` is only validated as non-zero (`Participant::new` rejects only `0`), so a participant index `> n` — or any index absent from the commitments map — causes a panic instead of an error. A single crafted `VerifyBlame`-style accusation therefore reliably crashes any party asked to adjudicate blame, matching the CVE's class: a low-privileged network peer causing a repeatable crash (complete DoS) via protocol input.

### Finding Description
`Participant` deserialization enforces only non-zero-ness:

```rust
// crypto/dkg/src/lib.rs:121-126
impl borsh::BorshDeserialize for Participant {
  fn deserialize_reader<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Participant::new(u16::deserialize_reader(reader)?)
      .ok_or_else(|| io::Error::other("invalid participant"))
  }
}
```

In `blame_internal`, the `sender` index is used directly as a `HashMap` key:

```rust
// crypto/dkg/pedpop/src/lib.rs:595-604
if !bool::from(
  multiexp_vartime(&share_verification_statements::<C>(
    recipient,
    &self.commitments[&sender],   // panics if `sender` is not 1..=n
    Zeroizing::new(share),
  ))
  .is_identity(),
) {
  return sender;
}
```

`self.commitments` is populated only for indexes `1..=n` (see `AdditionalBlameMachine::new`, `for i in 1 ..= n`, and `verify_r1`'s use of `all_participant_indexes()`). Neither `blame` nor `blame_internal` bounds-checks `sender`/`recipient` against `n`; the `EncryptedMessage` (`msg`) and `proof` are read from untrusted bytes via `EncryptedMessage::read` / `EncryptionKeyProof::read`. Any `sender` value that is a valid nonzero `Participant` but `> n` (e.g., `Participant(0xffff)`) reaches `self.commitments[&sender]` and panics. The same unchecked indexing exists in `calculate_share` is guarded by `validate_map`, but the blame path has no equivalent guard — the doc comment on `AdditionalBlameMachine::new` even notes invalid inputs "may cause ... panics," yet `blame` itself performs no validation of the participant arguments.

### Impact Explanation
Every honest node that attempts to verify a blame claim (the mechanism intended to slash a faulty DKG participant) panics on `HashMap` index failure. Because the trigger is a single malformed accusation message with an out-of-range `sender`/`accused` index, the crash is deterministic and repeatable by any party able to submit a blame message — the exact "hang or frequently repeatable crash (complete DoS)" profile of CVE-2024-21196. Crashing the blame adjudication path also neuters the slashing mechanism: accusing a malicious participant becomes impossible without taking down the verifiers.

### Likelihood Explanation
The reachable inputs are `sender: Participant`, `recipient: Participant`, and the serialized `EncryptedMessage`/proof — all attacker-controlled bytes fed to `EncryptedMessage::read` and then to `blame`. `Participant` accepts any nonzero `u16`, so no special key material, collusion, or privileged position is required. The panic triggers whenever blame verification runs with an out-of-set index; there is no ordering or race requirement.

### Recommendation
Validate `sender` and `recipient` in `blame`/`blame_internal` (and `AdditionalBlameMachine::new` consumers) against `1..=n` before indexing `self.commitments` — e.g., use `self.commitments.get(&sender)` and treat a missing entry as identifying the provided index as faulty (or return a `PedPoPError::InvalidParticipant`/`DkgError::InvalidParticipant` error). Mirror the `ThresholdParams::new` check `i.0 > n` rather than relying on `HashMap` indexing panics.

### Proof of Concept
```rust
// Participant space n = 3 (t = 2). commitments map only has keys 1, 2, 3.
// Attacker supplies sender = Participant(0xffff) — valid per Participant::new,
// absent from the map.

// AdditionalBlameMachine is constructed from the (valid, authenticated)
// commitment messages for participants 1..=n.
let machine = AdditionalBlameMachine::<Ristretto>::new(context, /* n */ 3, commitment_msgs)
  .unwrap();

// Craft any well-formed EncryptedMessage (contents don't matter for the panic;
// it only needs to read successfully and decrypt far enough to reach the
// commitments lookup, or the sender-blame path via a garbage proof).
let msg: EncryptedMessage<Ristretto, SecretShare<_>> =
  EncryptedMessage::read(&mut attacker_bytes, params).unwrap();

// sender = Participant(0xffff), recipient = Participant(1)
// -> blame_internal -> self.commitments[&Participant(0xffff)]
// -> thread 'main' panicked at 'index not found in HashMap'
let _faulty = machine.blame(Participant::new(0xffff).unwrap(), Participant::new(1).unwrap(), msg, None);
```

Note: the panic occurs at the `self.commitments[&sender]` lookup (or equivalently a malformed-message path where `decrypt_with_proof` succeeds enough to reach it). Even where an early return occurs, the `AdditionalBlameMachine::new(...).unwrap()` pattern used by consumers (e.g., `VerifyBlame` handling) compounds the issue, since `MissingParticipant` errors from missing commitments also panic at the `unwrap` call site.