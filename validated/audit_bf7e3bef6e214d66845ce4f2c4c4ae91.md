### Title
Missing encryption-key registration causes an index panic instead of an error when encrypting DKG shares — remote denial of service by an uncooperative DKG participant - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
CVE-2018-2805 is a remotely-triggerable crash/hang (complete availability loss) caused by a low-privileged network peer feeding the server input it mishandles. The Serai analog is in PedPoP's per-participant encryption box: `Encryption::encrypt` and `Decryption::decrypt_with_proof` index `self.decryption.enc_keys` / `self.enc_keys` with `[&participant]` / `[&decryptor]`, panicking if the referenced participant never registered an `EncryptionKeyMessage`. `Decryption::register` likewise uses `assert!` on re-registration. A DKG participant who simply withholds their encryption-key message (or whose message arrives late / is deduplicated by the caller) turns the honest node's share-encryption step into a `HashMap` index panic, crashing the validator instead of returning a blamable error. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`Encryption::encrypt` derives the per-recipient shared key as `ecdh(&self.enc_key, self.decryption.enc_keys[&participant])`. `enc_keys` is only populated via `Decryption::register`, which inserts `msg.enc_key` for each participant that supplied an `EncryptionKeyMessage`. There is no `Err` path for an absent key: indexing a `HashMap` on a missing key panics. Likewise, `decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` when verifying a blame proof, and `register` panics via `assert!(!self.enc_keys.contains_key(&participant))` if a key is registered twice for the same participant.

The library explicitly acknowledges it cannot enforce one-message-per-participant on the wire ("this library does not handle networking, it is unable to detect if any participant is so faulty"), so malformed/duplicated/missing encryption-key messages are an expected, attacker-controlled input class. Every other malformed-input path in the crate (`Commitments::read`, `EncryptedMessage::read`, `Participant::new`, `ThresholdParams::new`) correctly returns `io::Error`/`DkgError`; these three `HashMap`/`assert!` sites are the only ones that abort the process on peer-controlled state.

### Impact Explanation
An unprivileged DKG participant reaches the panic purely with the messages it does (or does not) send:

- Omit its `EncryptionKeyMessage` (or send one that fails deserialization so the caller never registers it) while other participants proceed to `encrypt` a share addressed to it → `enc_keys[&participant]` panics → node crash mid-DKG.
- Submit a second `EncryptionKeyMessage` for the same participant index (the caller forwards both since the library cannot detect the fault) → `assert!` in `register` panics.
- Trigger blame flow where `decrypt_with_proof` is invoked with a `decryptor` that has no registered key → `enc_keys[&decryptor]` panics.

Each panic kills the signing/validator task (and under `panic = "abort"` or unwinding into a non-catching executor, the node). Repeated across DKG sessions, an attacker prevents key generation / rotation entirely — a repeatable, low-cost remote DoS matching the CVE's "hang or frequently repeatable crash" class. Since DKG liveness gates all downstream FROST signing and chain operations, availability impact is broad; no key material is needed by the attacker beyond participation eligibility.

### Likelihood Explanation
Requires only the ability to participate in (or send malformed traffic into) a PedPoP session — the same low-privilege network access as the CVE (PR:L, AC:L). No cryptographic break, no collusion, and no special timing is needed: simply not sending, or double-sending, a message is enough. Panics in Rust libraries are reachable by default; only a caller explicitly wrapping every call in `catch_unwind` avoids the crash. The one mitigating factor is that a perfectly-behaving caller could pre-validate registrations, but the API surface invites the panic: `encrypt` accepts a bare `Participant` with no way to check registration beforehand, and `register`'s duplicate-check is an `assert!` rather than a returned error.

### Recommendation
- Replace `self.decryption.enc_keys[&participant]` in `Encryption::encrypt` with a lookup returning `io::Result`/`Result<(), DkgError>` (e.g., `enc_keys.get(&participant).ok_or(...)`), so a missing key becomes a blamable `FrostError`-style fault, not a crash.
- Replace `self.enc_keys[&decryptor]` in `decrypt_with_proof` the same way, returning `DecryptionError::InvalidProof`.
- Change `Decryption::register` to return `Result<M, DkgError>` and signal duplicate registration as an error (faulty participant) instead of `assert!`.
- Optionally add `Encryption::is_registered(participant) -> bool` so callers can pre-screen before encrypting.

### Proof of Concept
```rust
// Conceptual PoC (driver omitted): honest node runs PedPoP for params t=2, n=3, i=1.
// Malicious Participant(3) never sends an EncryptionKeyMessage (or sends garbage bytes
// that fail EncryptionKeyMessage::read, so register() is never called for them).

// Node state: enc_keys = { 1: k1, 2: k2 }  -- participant 3 absent.

// When the node distributes shares:
encryption.encrypt(&mut rng, Participant::new(3).unwrap(), share_msg);
//   -> encrypt() evaluates self.decryption.enc_keys[&participant]
//   -> HashMap index on missing key -> panic!("key not found") -> process/thread crash.

// Variant B: Participant(3) sends two EncryptionKeyMessages; caller forwards both:
decryption.register(p3, msg_a); // ok
decryption.register(p3, msg_b); // assert!(!enc_keys.contains_key(&p3)) -> panic

// Variant C: blame resolution with attacker-chosen/unregistered decryptor index:
decryption.decrypt_with_proof(from, unregistered_p, msg, Some(proof));
//   -> self.enc_keys[&decryptor] -> panic before any DLEq verification.
```

### Citations

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
