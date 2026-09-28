### Title
Unvalidated `sender`/`recipient` participant indexes in `AdditionalBlameMachine::blame` cause a panic (DoS) - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
Analogous to CVE-2023-34872 (a crash in `OutlineItem::open` reachable via attacker-crafted input), `AdditionalBlameMachine::blame` — the PedPoP blame adjudication API — indexes internal `HashMap`s with caller-supplied `sender`/`recipient` `Participant` values without checking they are members of the DKG set `1 ..= n`. A blame/accusation message naming an out-of-range participant deterministically panics the evaluating party (or any node verifying blame), crashing the process.

### Finding Description
`AdditionalBlameMachine::new(context, n, commitment_msgs)` builds `self.0.commitments` and `self.0.encryption.enc_keys` containing only the participants `1 ..= n` [1](#0-0) . `blame(sender, recipient, msg, proof)` forwards both arguments straight to `blame_internal` with no membership check [2](#0-1) .

`blame_internal` then panics on two paths:

1. `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)` accesses `self.enc_keys[&decryptor]` — an indexing panic when `recipient` (the accuser) is not a registered participant — whenever a `proof` is supplied [3](#0-2) .
2. `self.commitments[&sender]` inside `share_verification_statements` — an indexing panic when `sender` (the accused) is not in `1 ..= n` — reached whenever the message decrypted to a valid scalar [4](#0-3) .

`Participant` is only guaranteed non-zero (e.g. `Participant::new(read_u16()?)` in `ThresholdKeys::read` accepts any `1 ..= u16::MAX`), so an accuser/accused index of `n + 1 ..= 65535` passes all type-level validation and reaches the panicking index [2](#0-1) . `BlameMachine::blame` (the in-protocol path) has the same unchecked indexing.

### Impact Explanation
Any party evaluating a blame claim — an honest processor handling a `VerifyBlame` message, or any node asked to adjudicate an accusation — hits an unconditional `HashMap` index panic. Since Serai's deployment model treats a task panic as fatal (the panic-hook/exit pattern used across its binaries), a single maliciously formed accusation naming a non-existent participant crashes the validator/processor evaluating it — a remote, low-cost denial of service identical in class to the Poppler crash. Because `blame()` consumes attacker-influenced `accuser`/`accused` fields directly from the accusation message, the trigger requires no collusion and no valid key material.

### Likelihood Explanation
The accusation (`accuser`, `accused`) fields of a blame message are public attacker-chosen inputs; nothing in `AdditionalBlameMachine::blame` or `blame_internal` constrains them to the registered set. For the `commitments[&sender]` path, the accuser additionally needs a well-formed `EncryptedMessage` whose PoP verifies under a claimed `key` — trivially constructible by any party that can produce a Schnorr signature over bytes it controls, since `pop` is verified against `msg.key`, not a registered key [5](#0-4) . The `enc_keys[&decryptor]` path needs only a `proof` of `Some(_)` with an out-of-range `recipient`, crashing before the DLEq result is even usable. Either way, one message → deterministic panic.

### Recommendation
In `blame_internal` (and `AdditionalBlameMachine::blame`), reject `sender`/`recipient` values not present in `self.commitments`/`self.enc_keys` before indexing — e.g. `let Some(sender_commitments) = self.commitments.get(&sender) else { return sender }` and `get(&decryptor)` in `decrypt_with_proof`. An out-of-range accused is itself faulty, so blaming the accuser (or returning a distinct `InvalidAccusation` result) is appropriate; either way it must be an error path, not a panic.

### Proof of Concept
```rust
// After a PedPoP DKG among participants 1..=n, any party evaluating blame calls:
//   AdditionalBlameMachine::new(context, n, commitment_msgs).unwrap()
//     .blame(accuser, accused, msg, proof)
// with accuser/accused taken from the accusation message.

// Path A — out-of-range accused (`sender`), valid msg + key:
let accused = Participant::new(n + 1).unwrap(); // parses fine, never registered
machine.blame(accuser, accused, attacker_msg, None);
// -> panic at `self.commitments[&sender]` (lib.rs:599)

// Path B — out-of-range accuser (`recipient`), proof supplied:
let accuser = Participant::new(0xffff).unwrap();
machine.blame(accuser, real_accused, attacker_msg, Some(proof));
// -> panic at `self.enc_keys[&decryptor]` (encryption.rs:388)
```
Both panics abort the blaming participant (or third-party verifier) — a denial of service reachable with attacker-crafted public inputs.

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L595-604)
```rust
    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L654-661)
```rust
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
```

**File:** crypto/dkg/pedpop/src/lib.rs (L674-682)
```rust
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
```
