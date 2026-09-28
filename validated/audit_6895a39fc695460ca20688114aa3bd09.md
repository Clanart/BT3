### Title
Panic (index-out-of-map) in `AdditionalBlameMachine::blame` / `BlameMachine::blame` via attacker-controlled participant indexes causes node crash / DoS — (File: crypto/dkg/pedpop/src/lib.rs, crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Pinot advisory (GHSA-29f8-q7mf-7cqj, CWE-674 class) describes a crafted request to an exposed endpoint causing disruption of service. The analog in Serai: the public blame-resolution APIs `BlameMachine::blame`, `AdditionalBlameMachine::blame`, and the internal `blame_internal`/`decrypt_with_proof` take attacker-controlled `sender`/`recipient` `Participant` values and use them as unchecked `HashMap` indexes (`self.commitments[&sender]`, `self.enc_keys[&decryptor]`), panicking on out-of-set indexes instead of returning an error. Any party that can submit a blame accusation (a public, unauthenticated-by-design blame statement) can crash the evaluating process.

### Finding Description
`blame_internal` in `crypto/dkg/pedpop/src/lib.rs` computes share-verification statements by indexing `self.commitments[&sender]` (line ~599), which panics if `sender` was not a registered participant in `AdditionalBlameMachine::new` (populated only for `1..=n`, lines 654-660). Likewise, `Decryption::decrypt_with_proof` in `crypto/dkg/pedpop/src/encryption.rs` indexes `self.enc_keys[&decryptor]` (line 388) before any range check, panicking when the `recipient` has no registered encryption key. `Participant::new` only rejects `0`; values up to `u16::MAX` are valid types, and nothing in `blame`/`blame_internal`/`decrypt_with_proof` bounds the indexes to `<= n` or checks map membership before indexing:

- `self.enc_keys[&decryptor]` — encryption.rs:388, panics for unregistered recipient.
- `self.commitments[&sender]` — lib.rs ~599 (inside `share_verification_statements::<C>(recipient, &self.commitments[&sender], ...)`), panics for sender not in the commitment map.
- `Decryption::register` (encryption.rs:356-359) also `assert!`s on re-registration, another panic path if a caller is tricked into registering a participant twice.

Contrast with the rest of the DKG surface, which carefully returns typed errors (`DkgError::InvalidParticipant`, `MissingParticipant`, etc.) — these panic paths are inconsistent with that handling and are reachable purely from arguments (`sender`, `recipient`, `msg`, `proof`) supplied to a public API. `AdditionalBlameMachine` is explicitly designed to be run by a *non-participant* observer evaluating arbitrary blame claims, so hostile input to it is expected by design.

### Impact Explanation
A panic aborts the executing thread; in practice this crashes the coordinator/validator process evaluating blame (or at minimum kills the blame-evaluation task). This matches the advisory's impact: unauthenticated request → service disruption (availability loss). For a threshold DKG, crashing nodes during the blame/settlement phase can stall key generation or multisig rotation indefinitely, and repeated crafted blame statements can keep the set from recovering. No secret leakage or forgery is needed — it is a pure availability analog of the Pinot issue.

### Likelihood Explanation
Blame statements are, by construction, distributed to third parties (`AdditionalBlameMachine::new` exists for observers "regardless of if the caller was a member"), and the `sender`/`recipient`/`msg`/`proof` arguments are attacker-chosen. Any party — including a non-participant relaying a fabricated blame tuple with `sender` or `recipient` index `> n` — triggers the panic deterministically on first evaluation. Only requirement is that the target evaluates blame, which is the documented purpose of these APIs.

### Recommendation
Replace `HashMap` indexing with checked lookups returning a `PedPoPError`/`DecryptionError`:

- In `decrypt_with_proof`, use `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)` (or a dedicated `UnknownParticipant` variant).
- In `blame_internal`, validate `sender`/`recipient` against the known participant set (or use `.get(&sender)` on `commitments`) before indexing; treat unknown indexes as an error/faulty-accuser result, never a panic.
- Similarly, downgrade `Decryption::register`'s `assert!` on duplicate registration to a returned error.

### Proof of Concept
```rust
// Setup: build an AdditionalBlameMachine for a small set (e.g. n = 3)
let n = 3u16;
let commitment_msgs: HashMap<Participant, EncryptionKeyMessage<_, Commitments<_>>> = /* ... */;
let machine = AdditionalBlameMachine::new(context, n, commitment_msgs).unwrap();

// Attacker-supplied blame claim referencing a non-existent participant
let bogus_sender = Participant::new(10).unwrap(); // > n
let bogus_recipient = Participant::new(7).unwrap(); // no registered enc key

// Path A: if msg.pop verification passes (attacker knows key), the DLEq verify
// evaluates `self.enc_keys[&decryptor]` -> index panic on `bogus_recipient`
// Path B: if the PoP/DLEq happen to proceed to share verification,
// `self.commitments[&bogus_sender]` -> index panic
machine.blame(bogus_sender, bogus_recipient, forged_msg, Some(forged_proof));
// Both paths panic via HashMap indexing instead of returning a blame result.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
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

**File:** crypto/dkg/pedpop/src/lib.rs (L575-608)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

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
    }

    // The share was canonical and valid
    recipient
```

**File:** crypto/dkg/pedpop/src/lib.rs (L649-662)
```rust
  pub fn new(
    context: [u8; 32],
    n: u16,
    mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<Self, PedPoPError<C>> {
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
  }
```
