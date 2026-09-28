### Title
Unvalidated blame recipient causes panic and denial of service - ([File: `crypto/dkg/pedpop/src/encryption.rs`](crypto/dkg/pedpop/src/encryption.rs))

### Summary

`AdditionalBlameMachine::blame` accepts attacker-controlled `sender` and `recipient` participant IDs and forwards them to `blame_internal`. During proof evaluation, `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` before validating that `decryptor` is a registered DKG participant. Because `enc_keys` only contains participants `1..=n`, supplying `recipient = n + 1` causes a panic.

### Finding Description

`AdditionalBlameMachine::new` populates `Decryption::enc_keys` only from the authenticated commitment messages for participants `1..=n` [1](#0-0) . The public `AdditionalBlameMachine::blame` API passes the supplied `sender` and `recipient` directly to `blame_internal` without checking either participant ID against the configured set [2](#0-1) . `blame_internal` then calls `decrypt_with_proof` with that unchecked recipient [3](#0-2) .

Inside `decrypt_with_proof`, once the message's proof-of-possession signature verifies, a supplied `EncryptionKeyProof` causes `self.enc_keys[&decryptor]` to be evaluated as an argument to `DLEqProof::verify` [4](#0-3) . `HashMap` indexing panics for an absent key. Therefore, a blame request naming any participant outside `1..=n` crashes instead of returning a blame/error result.

### Impact Explanation

A remote party able to submit a blame adjudication request can reliably panic the process or task handling PedPoP blame. The malicious message can be a legitimate, previously received `EncryptedMessage` from a valid sender, so it passes the initial proof-of-possession check; the malformed `recipient` then reaches the unchecked map lookup [5](#0-4) . Repeated requests can repeatedly crash workers and prevent DKG completion or dispute resolution. This is a reachable availability failure matching the reported bug class.

### Likelihood Explanation

The trigger requires only:

1. A valid `EncryptedMessage` whose `pop` verifies for the supplied `sender`.
2. Any parseable `EncryptionKeyProof`; its cryptographic validity is not reached before the panic.
3. `recipient = Participant::new(n + 1)`, or any other unregistered non-zero participant ID.

`Participant::new` accepts every non-zero `u16`, while `enc_keys` only contains participants through `n` [6](#0-5) . No secret knowledge or cryptographic forgery is needed.

### Recommendation

Validate `sender` and `recipient` before evaluating blame. In particular, `blame_internal` or `Decryption::decrypt_with_proof` should use `HashMap::get` and return a structured error when the participant is not registered, rather than indexing the map. The same validation should apply to lookups of `self.commitments[&sender]`, which also panic for unknown senders [7](#0-6) .

### Proof of Concept

```rust
// n = 2, and commitments were registered for participants 1 and 2.
let machine = AdditionalBlameMachine::<Secp256k1>::new(
    context,
    2,
    commitment_msgs,
)?;

// A valid encrypted share previously sent by participant 1.
let msg: EncryptedMessage<Secp256k1, SecretShare<_>> =
    EncryptedMessage::read(&mut valid_share_bytes, params)?;

// Any syntactically valid proof encoding is sufficient; its DLEq check is not
// reached before the panic.
let proof = EncryptionKeyProof::read(&mut proof_bytes)?;

// Participant 3 is not in the n = 2 DKG.
let invalid_recipient = Participant::new(3).unwrap();

// Panics inside decrypt_with_proof on `self.enc_keys[&decryptor]`.
machine.blame(sender, invalid_recipient, msg, Some(proof));
```

The panic occurs because `invalid_recipient` is absent from `enc_keys`, and the code indexes it directly while assembling the DLEq verification inputs [8](#0-7) .

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L575-583)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-605)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L656-660)
```rust
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L674-681)
```rust
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-390)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }

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

**File:** crypto/dkg/src/lib.rs (L29-35)
```rust
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```
