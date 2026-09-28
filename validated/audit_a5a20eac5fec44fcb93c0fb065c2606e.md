### Title
Panic on duplicate encryption-key registration crashes DKG participant (unauthenticated remote DoS) - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
`Decryption::register` in `crypto/dkg/pedpop/src/encryption.rs` uses `assert!` to reject a second `EncryptionKeyMessage` for the same `Participant`. A malicious DKG participant only has to send two registration messages (each containing a well-formed `EncryptionKeyMessage`, which passes `read`/`C::read_G` cleanly) to hit `assert!(!self.enc_keys.contains_key(&participant))` and panic the host process — a reachable, remote, unconditional denial of service, analogous to the parser-triggered crash in CVE-2025-21575. [1](#0-0) 

### Finding Description
`Encryption::register` forwards every received `EncryptionKeyMessage` to `Decryption::register`:

```rust
assert!(
  !self.enc_keys.contains_key(&participant),
  "Re-registering encryption key for a participant"
);
self.enc_keys.insert(participant, msg.enc_key);
``` [2](#0-1) 

`EncryptionKeyMessage::read` performs no deduplication — it only parses the inner message and one group element, both of which succeed for any well-formed encoding: [3](#0-2) 

The library explicitly states it does not handle networking and cannot detect a participant sending multiple messages ("If any participant sends multiple sets of commitments, they are faulty... That responsibility lies with the caller"), so a caller that faithfully processes each received message will call `register` twice for the same `Participant` and hit the panic: [4](#0-3) 

Elsewhere the crate treats protocol violations as recoverable errors (`io::Error` on invalid encodings, `DecryptionError` for bad proofs, `FrostError` for invalid signing sets). Here the same class of peer misbehavior is enforced with `assert!`, which aborts/panics instead of returning an error.

### Impact Explanation
Any DKG participant — an unauthenticated party whose only capability is sending messages in the protocol — can crash every honest participant's process by broadcasting a second encryption-key registration. For a threshold-network node this is a complete availability loss: the process dies mid-protocol and (depending on the integrator's panic handling) cannot resume the DKG, mirroring CVE-2025-21575's "hang or frequently repeatable crash (complete DOS)" impact. It is repeatable on demand and requires no special privileges, valid key shares, or malformed encodings.

### Likelihood Explanation
Trivially exploitable: the attacker needs a participant slot in the DKG (the same trust level as every other PedPoP peer) and sends two syntactically valid `EncryptionKeyMessage`s. The cost is two protocol messages; no race, no probabilistic condition. The only mitigating factor is whether integrators deduplicate per-participant messages before calling `register` — but the crate's own documentation places duplicate detection on the caller while still panicking on the duplicate, making the crash a realistic outcome rather than a documented-MUST misuse: the API offers no non-panicking path to detect or reject a duplicate.

### Recommendation
Replace the `assert!` in `Decryption::register` with a fallible check returning `io::Error`/`DecryptionError` (or a dedicated `DuplicateParticipant` error), so callers can attribute blame and continue:

```rust
pub(crate) fn register<M: Message>(...) -> Result<M, Error> {
  if self.enc_keys.contains_key(&participant) {
    return Err(Error::DuplicateParticipant(participant));
  }
  ...
}
```

Same for the `self.enc_keys[&participant]` / `self.enc_keys[&decryptor]` indexing in `encrypt`/`decrypt_with_proof`, which also panics if a participant index is absent (e.g., a blame procedure targeting a `decryptor` who never registered). [5](#0-4) [6](#0-5) 

### Proof of Concept
```rust
// Attacker is Participant(2) in a PedPoP DKG.
// They send EncryptionKeyMessage twice to the same honest node.

// Honest node's handling loop (per received message):
let msg1 = EncryptionKeyMessage::<C, M>::read(&mut bytes1, params)?;
encryption.register(Participant::new(2).unwrap(), msg1); // ok

let msg2 = EncryptionKeyMessage::<C, M>::read(&mut bytes2, params)?;
encryption.register(Participant::new(2).unwrap(), msg2);
// thread panics at:
//   "Re-registering encryption key for a participant"
// Process aborts -> complete DoS of the DKG participant.
```

The second message requires no cryptographic validity — `EncryptionKeyMessage::read` succeeds for any well-formed `M` plus any valid `C::G` encoding, so `enc_keys.insert` is reached unconditionally before the assert fires.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L383-390)
```rust
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L452-458)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    self.decryption.register(participant, msg)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L96-101)
```rust
/// The commitments message, intended to be broadcast to all other parties.
///
/// Every participant should only provide one set of commitments to all parties. If any
/// participant sends multiple sets of commitments, they are faulty and should be presumed
/// malicious. As this library does not handle networking, it is unable to detect if any
/// participant is so faulty. That responsibility lies with the caller.
```
