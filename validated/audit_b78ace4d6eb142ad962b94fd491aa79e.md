The CVE class is a NULL-pointer dereference on malformed/unexpected input — in Rust terms, a reachable panic on attacker-controlled inputs. I found a direct analog in PedPoP's blame arbitration: `decrypt_with_proof` indexes `self.enc_keys[&decryptor]` and `blame_internal` indexes `self.commitments[&sender]` without checking that the supplied `Participant` was ever registered [1](#0-0) [2](#0-1) .

### Title
Panic (HashMap index on unregistered participant) in PedPoP blame arbitration — remote DoS via malformed blame accusation - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept `sender` and `recipient` as arbitrary `Participant` values and pass them to `blame_internal` → `Decryption::decrypt_with_proof`, which evaluates `self.enc_keys[&decryptor]` while building the DLEq verify arguments. `Participant::new` only rejects zero, so any index `> n` (or a non-member index below `n` for `AdditionalBlameMachine` built with a subset) is a valid `Participant` but absent from the map, causing a panic. Similarly, `blame_internal` indexes `self.commitments[&sender]`. The panic fires before/while the supplied `EncryptionKeyProof` is verified, so the attacker needs no valid proof — only a syntactically valid `EncryptedMessage` whose `pop` Schnorr signature verifies (any real share message qualifies, since `blame` takes the sender's published message) plus arbitrary bytes for `proof` (`EncryptionKeyProof::read` only does `read_G`/`DLEqProof::read` and never fails on well-formed-length input).

### Finding Description
- `Decryption::decrypt_with_proof`: `self.enc_keys[&decryptor]` at encryption.rs:388 panics for any `decryptor` not registered. Registration only happens for participants `1..=n` in `AdditionalBlameMachine::new` (lib.rs:656-660) or actual DKG members in `verify_r1` (lib.rs:313-315). `sender`/`recipient` are attacker-supplied in `blame` (lib.rs:623-632, 674-682).
- `blame_internal`: `self.commitments[&sender]` at lib.rs:599 panics for a `sender` outside the DKG set — reached when the pop signature verifies (attacker replays the real encrypted share that `sender` published).
- `AdditionalBlameMachine` is explicitly documented as usable "regardless of if the caller was a member of the DKG protocol" (lib.rs:639-641), so this path is reachable by parties who merely hold the public commitment messages — i.e., an unprivileged third party submitting a blame accusation.

### Impact Explanation
Deterministic panic in an `io`-safe API surface. Any node arbitrating a blame claim (a coordinator/processor evaluating `AdditionalBlameMachine::blame`) crashes instead of returning a `Participant` verdict. This aborts DKG fault resolution and can crash the hosting process — a remote denial of service triggered purely by attacker-chosen `sender`/`recipient` values, mirroring CVE-2017-15721's NULL-deref-on-malformed-message class.

### Likelihood Explanation
Any party able to submit a blame accusation (including non-participants, per the documented `AdditionalBlameMachine::new` contract) can trigger it with `recipient = Participant::new(n+1)` and a replayed valid `EncryptedMessage` from any real sender. No valid DLEq proof is needed since the indexing precedes verification success.

### Recommendation
In `decrypt_with_proof` and `blame_internal`, replace `self.enc_keys[&decryptor]` / `self.commitments[&sender]` with `.get(..)` lookups, returning `DecryptionError::InvalidProof` / blaming `recipient` (or a dedicated `InvalidParticipant` error) when absent.

### Proof of Concept
```rust
// params: t=2, n=3, i=1..3 DKG already completed
// honest sender=2 published EncryptionKeyMessage + EncryptedMessage<SecretShare>
let attacker = /* any party holding the public commitment messages */;
let machine = AdditionalBlameMachine::<Secp256k1>::new(context, 3, commitment_msgs).unwrap();
let bogus_recipient = Participant::new(4).unwrap(); // valid Participant, never registered
let proof: EncryptionKeyProof<Secp256k1> =
  EncryptionKeyProof::read(&mut arbitrary_valid_len_bytes.as_slice()).unwrap();
// panics at self.enc_keys[&decryptor] inside decrypt_with_proof
machine.blame(Participant::new(2).unwrap(), bogus_recipient, msg_from_2, Some(proof));
```

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

**File:** crypto/dkg/pedpop/src/lib.rs (L595-608)
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
    }

    // The share was canonical and valid
    recipient
```
