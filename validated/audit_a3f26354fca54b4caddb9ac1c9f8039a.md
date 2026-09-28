### Title
Unauthenticated participant index causes panic (node crash) in PedPoP blame decryption - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` indexes `self.enc_keys` with a caller/attacker-supplied `Participant` (`decryptor`) using unchecked `HashMap` indexing (`self.enc_keys[&decryptor]`). If the referenced participant has no registered encryption key, the node panics, producing a remotely triggerable crash — the same availability bug class as CVE-2019-2455 (low-privileged, network-reachable denial of service). [1](#0-0) 

### Finding Description
`Decryption` stores per-participant encryption keys in `enc_keys: HashMap<Participant, C::G>`, populated exclusively through `register`, which inserts `msg.enc_key` for each participant whose `EncryptionKeyMessage` was processed [2](#0-1) .

When resolving a blame accusation, `decrypt_with_proof` verifies the accuser's `EncryptionKeyProof` DLEq against `self.enc_keys[&decryptor]`:

```rust
proof
  .dleq
  .verify(
    &mut encryption_key_transcript(self.context),
    &[C::generator(), msg.key],
    &[self.enc_keys[&decryptor], *proof.key],
  )
``` [3](#0-2) 

There is no `contains_key`/`.get()` guard. `Participant` values are only validated as non-zero `u16`s at parse time (e.g., `Participant::new(...)` in the blame/accusation message path), so `decryptor` can be any participant index — including one whose `EncryptionKeyMessage`/`Commitments` were never received, never parsed successfully, or never registered. `HashMap`'s `Index` impl panics on a missing key, aborting the calling thread.

The analogous unguarded index exists in `Encryption::encrypt` (`self.decryption.enc_keys[&participant]`) [4](#0-3)  and in `KeyMachine::calculate_share` via `self.commitments[&l]` [5](#0-4) , though `calculate_share` is protected by `validate_map` against the full participant set. The blame path, reached through `BlameMachine`/`AdditionalBlameMachine::blame(accuser, accused, share, proof)`, is driven by attacker-chosen `accuser`/`faulty` fields and attacker-supplied `EncryptedMessage`/`EncryptionKeyProof` bytes (`EncryptedMessage::read`, `EncryptionKeyProof::read`, `DLEqProof::read` are all attacker-facing deserializers) [6](#0-5) [7](#0-6) . A participant who never successfully registered an encryption key can still be named as `accuser`/`decryptor` in an accusation, hitting the missing-key panic before any signature check on the DLEq path completes.

### Impact Explanation
A panic inside the blame/decryption path crashes the processor thread handling DKG fault resolution. Because panic aborts propagate out of library code into the host process, a single crafted accusation naming an unregistered participant index takes down the node mid-DKG — an unauthorized, repeatable crash (complete DoS of the signing/key-gen pipeline), directly matching the CVE-2019-2455 impact profile (CVSS 6.5, availability-only). While the thread is down, honest participants cannot complete `calculate_share`/`complete` or resolve blame, halting threshold key generation.

### Likelihood Explanation
The attacker needs only to be a participant able to submit a blame/accusation message naming a `decryptor` index that is absent from `enc_keys` — e.g., their own index when they never delivered a parseable `EncryptionKeyMessage`, or any `Participant::new(k)` value whose registration never occurred. No threshold cooperation, valid signature, or valid proof is required: the `enc_keys[&decryptor]` index is evaluated inside `dleq.verify` argument construction, so the panic fires regardless of whether the supplied `EncryptionKeyProof` is valid. No secret material or cryptographic break is needed — just attacker-chosen participant fields.

### Recommendation
Replace the unchecked indexing with a fallible lookup and return `DecryptionError`/`PedPoPError` on a missing entry:

```rust
let Some(enc_key) = self.enc_keys.get(&decryptor) else {
  Err(DecryptionError::InvalidProof)?
};
```

Apply the same pattern to `Encryption::encrypt`'s `enc_keys[&participant]` and audit other `HashMap` index uses on attacker-influenced `Participant` keys (`self.commitments[&l]`). Additionally, validate at the `blame`/`decrypt_with_proof` call sites that `accuser`/`decryptor`/`faulty` are members of the registered participant set before use.

### Proof of Concept
Conceptual reproduction (requires a `Decryption` instance where participant `p` was never registered):

```rust
// enc_keys contains entries for participants {1, 2}; participant 3 never registered.
// A blame resolution calls:
decryption.decrypt_with_proof(
  from,                  // accused participant
  Participant::new(3).unwrap(),  // decryptor — unregistered index
  msg,                   // any EncryptedMessage with a *valid* PoP,
                         // or reached after pop.verify via blame path
  Some(any_proof),       // contents irrelevant — panic precedes DLEq math
);
// panics at `self.enc_keys[&decryptor]` (encryption.rs:388)
// "key not found" — thread abort
```

Note: the panic is reached only after `msg.pop.verify` passes (or via the code path constructing the `dleq.verify` arguments), so the supplied `EncryptedMessage` must carry a structurally valid Schnorr PoP for `msg.key` — which the accuser can always self-generate by encrypting any message under their own ephemeral key, exactly as `encrypt` does. The `EncryptionKeyProof` itself need not be valid; the index panic fires while building the `points` argument.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L266-269)
```rust
impl<C: Ciphersuite> EncryptionKeyProof<C> {
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
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
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Result<Zeroizing<E>, DecryptionError> {
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

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L487-491)
```rust
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
```
