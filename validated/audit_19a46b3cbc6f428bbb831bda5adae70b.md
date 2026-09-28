### Title
Panic via unauthenticated map indexing / re-registration assert on untrusted PedPoP messages — (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The OpenSSH CVE-2016-1907 bug class is "crafted network traffic reaches a packet-parsing path and crashes the process" (out-of-bounds read → application crash, CVSS availability impact). Serai's analog lives in PedPoP's encryption layer: `Decryption::register` enforces single-registration with a hard `assert!`, and `decrypt_with_proof` indexes `self.enc_keys[&decryptor]` with an attacker-influenced `Participant` index. Both sites panic rather than returning an error when fed crafted or adversarially sequenced messages, crashing the host process performing a DKG.

### Finding Description
Two panic sites are reachable from bytes an unprivileged party supplies to `EncryptionKeyMessage::read` / `EncryptedMessage::read` / `EncryptionKeyProof::read`:

1. `Decryption::register` panics on any second registration for the same participant: [1](#0-0) 
   `Encryption::register` forwards every received `EncryptionKeyMessage` here without deduplication, so a participant who transmits a second registration message (e.g., across retry/attempt boundaries or via message duplication on the assumed-authenticated-but-not-deduplicated channel) triggers the `assert!` and aborts the local node.

2. `Decryption::decrypt_with_proof` indexes the `enc_keys` map by an untrusted `decryptor` index: [2](#0-1) 
   If the `decryptor` `Participant` carried in the accusation message is not a previously registered participant (malformed index, or a message ordering where registration never completed), `self.enc_keys[&decryptor]` panics on the absent key instead of returning `DecryptionError`. The same pattern exists in `Encryption::encrypt` (`self.decryption.enc_keys[&participant]`, line 466).

Note that sibling deserialization paths correctly return `io::Error` for malformed input (`read_G` rejects non-canonical points, `ThresholdKeys::read` maps `ThresholdParams::new` failures to `io::Error::other`), so the panic sites are inconsistent error-handling, not intended rejection: [3](#0-2) 

### Impact Explanation
A remote party can force a panic/abort in any process running PedPoP DKG by (a) sending a duplicate `EncryptionKeyMessage` for an already-registered `Participant`, or (b) crafting an accusation/share path that reaches `decrypt_with_proof` with a `decryptor` index absent from `enc_keys`. This is a straightforward denial of service against a threshold-signing committee member: crashing a participant mid-DKG aborts key generation and can stall the whole committee (matching the CVE's availability-only, no-confidentiality-leak impact class). No secret is leaked; the impact is crash DoS — a Medium-severity analog of the OpenSSH `ssh_packet_read_poll2` OOB-read crash.

### Likelihood Explanation
The channel into PedPoP is authenticated per-participant but not necessarily deduplicated — the code itself only guards reuse via this `assert!`, implying the authors expected duplicate deliveries to be possible. Message retransmission across DKG `attempt` boundaries (the transaction format carries an `attempt` counter precisely because rounds are retried) makes duplicate deliveries realistic even without malice. For `decrypt_with_proof`, the `decryptor`/`from` indices originate from untrusted accusation message fields and are not checked against `enc_keys` membership before indexing. Both panics require only public, well-formed-to-the-parser bytes; no key material or collusion is needed.

### Recommendation
Replace panics with error returns at both sites:
- In `Decryption::register`, return `io::Error`/a `PedPopError` (or silently reject) when `enc_keys.contains_key(&participant)` instead of `assert!`.
- In `decrypt_with_proof` and `Encryption::encrypt`, use `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` rather than `HashMap` indexing.
- Audit other indexing/`assert!`/`unwrap` sites on message-handling paths in `crypto/dkg` and `crypto/dkg/pedpop` for the same pattern.

### Proof of Concept
```rust
// Conceptual: inside a PedPoP test with curve C = Ristretto.
// Two EncryptionKeyMessage registrations for the same Participant:
let mut enc = Encryption::<Ristretto>::new(context, our_i, &mut rng);
let msg = enc.registration(dummy_msg); // attacker-controlled serialized bytes
let parsed1 = EncryptionKeyMessage::<Ristretto, M>::read(&mut bytes1, params)?;
let parsed2 = EncryptionKeyMessage::<Ristretto, M>::read(&mut bytes2, params)?;
enc.register(attacker_p, parsed1);
enc.register(attacker_p, parsed2); // panics at encryption.rs:357 assert!

// Or: accusation referencing an unregistered decryptor:
decryption.decrypt_with_proof::<E>(
    accuser_p, Participant::new(0xFFFF).unwrap(), // never registered
    encrypted_msg, Some(proof),
); // panics on self.enc_keys[&decryptor] at encryption.rs:388
```

Uncertainty: I confirmed the panic sites and the read-path reachability within `crypto/dkg/pedpop`, but did not fully trace whether callers in `pedpop/src/lib.rs` pre-validate `participant`/`decryptor` membership before invoking `register`/`decrypt_with_proof`. If callers already reject unknown participants or deduplicate per `attempt`, the exposure narrows; however the internal `assert!` and unchecked `HashMap` indexing remain latent crash bugs reachable from any deserialized message the machine accepts.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L356-361)
```rust
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-392)
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
```

**File:** crypto/ciphersuite/src/lib.rs (L95-100)
```rust
    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```
