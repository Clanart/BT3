### Title
Out-of-range participant index in PedPoP blame evaluation panics, crashing any node verifying a blame claim - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2026-35235 is a remotely-triggerable crash/hang (complete DoS) caused by processing attacker-supplied structured input. The Serai analog is an index panic in the PedPoP DKG's blame machinery: `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept `sender` and `recipient` `Participant` indexes straight from a network blame claim, and index `self.enc_keys[&decryptor]` and `self.commitments[&sender]` without any bounds check. `Participant` accepts any non-zero `u16`, so any index `> n` panics the calling thread. `AdditionalBlameMachine` is explicitly designed to be run by non-members evaluating blame claims over the network, so this panic is reachable while processing untrusted blame data — crashing/hanging the processor that verifies it.

### Finding Description
`Decryption::decrypt_with_proof` verifies the message's PoP, then, when an `EncryptionKeyProof` is supplied, builds the DLEq statement with `self.enc_keys[&decryptor]` before the proof is even checked — a `HashMap` index that panics on a missing key [1](#0-0) . `blame_internal` passes the untrusted `recipient` (the accuser) as `decryptor` and later indexes `self.commitments[&sender]` with the untrusted `sender` (the accused) [2](#0-1) . `enc_keys` and `commitments` only contain entries `1..=n`, populated in `AdditionalBlameMachine::new` [3](#0-2) . Nothing validates that `sender`/`recipient` are `<= n`; `Participant::new` only rejects zero. The machine's own docs warn that invalid inputs "may cause everything from inaccurate blame to panics", confirming panics are reachable but the boundary is not enforced at this layer.

### Impact Explanation
A single blame claim naming a non-existent participant (e.g., `accuser = Participant(n+1)`), carrying any `EncryptedMessage` with a valid PoP (trivially constructed with a fresh key — the PoP is self-signed) and any syntactically-parseable `EncryptionKeyProof`, panics every processor that evaluates the blame via `AdditionalBlameMachine::blame`. Since blame verification is performed deterministically by the validator set to decide slashing, a panic here is a repeatable crash — the exact analog of the MySQL "frequently repeatable crash" DoS — aborting the DKG/key-management processing and potentially stalling validator set rotation.

### Likelihood Explanation
Triggering requires submitting a blame claim, which in deployment is authenticated to a validator and normally arises from a real fault report — so an attacker generally needs to be a participant (or induce one) and craft the claim with an out-of-range index. The message (valid PoP) and parseable proof are cheap to construct; the `enc_keys[&decryptor]` panic fires before DLEq verification, so no valid proof is needed for the `recipient` path. Severity Medium (availability only, requires a protocol participant position), mirroring the advisory's 4.9.

### Recommendation
In `BlameMachine::blame` / `AdditionalBlameMachine::blame` (or `blame_internal` / `decrypt_with_proof`), reject `sender`/`recipient` indexes not present in `self.commitments` / `self.enc_keys` — e.g., return the accusing party or an explicit error instead of indexing with `[]`. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `get(...)` lookups that return `DecryptionError`/a defined blame result on `None`.

### Proof of Concept
```rust
// n = 3 DKG; commitments for participants 1..=3 registered via AdditionalBlameMachine::new
let machine = AdditionalBlameMachine::<Ristretto>::new(context, 3, commitment_msgs).unwrap();

// Attacker crafts an EncryptedMessage with a valid PoP for a fresh per-message key
let msg: EncryptedMessage<Ristretto, SecretShare<_>> = /* key = g^k, valid Schnorr PoP, arbitrary ciphertext */;

// Any syntactically-parseable proof; DLEq is never verified because the map index panics first
let proof = Some(EncryptionKeyProof::read(&mut proof_bytes.as_slice()).unwrap());

// Accuser index 5 was never registered -> `self.enc_keys[&Participant(5)]` panics
let _ = machine.blame(Participant::new(5).unwrap(), Participant::new(2).unwrap(), msg, proof);
// thread panics inside decrypt_with_proof at encryption.rs (enc_keys[&decryptor])
```
Equivalently, with a valid `EncryptionKeyProof` computable by the accuser (who knows their own `enc_key`), an `accused` index `> n` panics at `self.commitments[&sender]` in `blame_internal` [4](#0-3) .

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-396)
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

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
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
