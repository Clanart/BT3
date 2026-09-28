### Title
Panic on duplicate encryption-key registration aborts the PedPoP DKG (remote DoS by a single participant) - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary

The upstream commit fixed a polling helper that `unwrap()`ed a fallible request, so one malformed/failed response panicked the caller. The analogous bug class in Serai is a reachable panic on input another (untrusted) protocol participant controls. `Decryption::register` in PedPoP uses a hard `assert!` to reject a second encryption-key registration for the same participant, and it is invoked on `EncryptionKeyMessage`s deserialized from other participants via `EncryptionKeyMessage::read`. Any participant who sends (or whose message is replayed as) two registration messages panics the honest node running PedPoP, aborting key generation.

### Finding Description

`Encryption::register` forwards each peer's `EncryptionKeyMessage` to `Decryption::register`, which panics on a second registration for the same `Participant`: [1](#0-0) 

```rust
assert!(
  !self.enc_keys.contains_key(&participant),
  "Re-registering encryption key for a participant"
);
self.enc_keys.insert(participant, msg.enc_key);
```

The messages feeding this are untrusted wire bytes: `EncryptionKeyMessage::read` accepts any serialized message plus an arbitrary `enc_key` group element with no authentication: [2](#0-1) 

Unlike the rest of PedPoP, which converts bad peer input into `io::Error`/verification failures (e.g. `Commitments::read` returning `io::Result`, `decrypt_with_proof` returning `DecryptionError`), this path converts a duplicate-message condition — exactly the kind of fault the library itself says it cannot detect (`Commitments` doc: "If any participant sends multiple sets of commitments, they are faulty... this library is unable to detect") — into a process panic: [3](#0-2) 

A related indexing panic exists at `decrypt_with_proof`, where `self.enc_keys[&decryptor]` panics if the referenced decryptor never registered: [4](#0-3) 

### Impact Explanation

A single faulty or malicious participant in the DKG — who only needs to emit a second `EncryptionKeyMessage` (validly serialized, trivially produced since `enc_key` is any `C::G`) — causes every honest node that processes it to panic inside `Encryption::register`. This halts key generation / resharing for the whole validator set. Because the panic is unconditional (`assert!`, not `debug_assert!`), it fires in release builds. Repeated across DKG retries it is a persistent denial of service of threshold key setup, preventing the group key and all downstream signing (FROST, bitcoin-serai outputs) from ever being established. This is a liveness/DoS issue consistent with Medium severity; no secret material is leaked.

### Likelihood Explanation

Triggering requires only that a participant emit two registration messages — no race, no threshold collusion, no valid proof needed (the `msg` and `enc_key` are not verified before the `assert!` runs). Duplicates can also arise non-maliciously (message retransmission on an unreliable channel, resend after timeout), which is precisely the "failed request while server is starting" scenario the upstream fix addressed. Any deployment performing PedPoP key generation with `n > 1` reachable participants is exposed.

### Recommendation

Replace the `assert!` with an error return. `Decryption::register` should return `io::Result<M>` (or a dedicated `RegistrationError::AlreadyRegistered(Participant)`) and reject the duplicate without inserting, letting the caller flag the participant as faulty — matching how `read_preprocess`/`read_share` failures are mapped to `InvalidParticipant`/`InvalidShare` elsewhere. Similarly, use `self.enc_keys.get(&decryptor)` in `decrypt_with_proof` and return `DecryptionError::InvalidProof` on absence.

### Proof of Concept

```rust
// Conceptual PoE against crypto/dkg/pedpop/src/encryption.rs
// An honest node `i` running PedPoP processes EncryptionKeyMessages:

// Participant `j` (any unprivileged participant) serializes one message twice:
let msg_bytes = /* EncryptionKeyMessage::<C, Commitments<C>> from participant j */;
let mut reg1 = EncryptionKeyMessage::read(&mut msg_bytes.as_slice(), params).unwrap();
let mut reg2 = EncryptionKeyMessage::read(&mut msg_bytes.as_slice(), params).unwrap();

// Honest node registers both for participant j:
encryption.register(j, reg1); // ok: inserts enc_keys[j]
encryption.register(j, reg2); // PANIC: assert!(!enc_keys.contains_key(&j))
// "Re-registering encryption key for a participant" -> process abort in release builds
```

The panic site is `crypto/dkg/pedpop/src/encryption.rs:356-360`; the attacker-controlled bytes enter through `EncryptionKeyMessage::read` at `encryption.rs:57-59`, which performs no deduplication or proof verification before `register` asserts.

Note: I verified the panic is reachable in the PedPoP message flow, but I could not fully trace every upstream caller (e.g. whether processor-level code deduplicates registrations before calling `Encryption::register`). If a caller already guarantees uniqueness, the exposure is limited to direct library consumers; the `assert!` on untrusted input still violates the library's own error-handling contract.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-60)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L96-107)
```rust
/// The commitments message, intended to be broadcast to all other parties.
///
/// Every participant should only provide one set of commitments to all parties. If any
/// participant sends multiple sets of commitments, they are faulty and should be presumed
/// malicious. As this library does not handle networking, it is unable to detect if any
/// participant is so faulty. That responsibility lies with the caller.
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct Commitments<C: Ciphersuite> {
  commitments: Vec<C::G>,
  cached_msg: Vec<u8>,
  sig: SchnorrSignature<C>,
}
```
