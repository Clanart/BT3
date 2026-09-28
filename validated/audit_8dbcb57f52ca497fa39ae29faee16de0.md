### Title
Reachable abort on duplicated `EncryptionKeyMessage` registration causes remote DoS of the PedPoP DKG - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary

CVE-2018-9154 is a reachable `abort()` in JasPer's decoder when untrusted input drives an unexpected allocation result, yielding remote denial of service. The Serai analog is a reachable `assert!` panic in `Decryption::register`, which aborts the host process when a participant submits an encryption-key registration message twice during a PedPoP DKG session. The condition is fully attacker-controlled: the aborting input is an ordinary protocol message from an unprivileged participant, and the code panics instead of returning an `Err` like every other malformed-message path in the crate.

### Finding Description

`Encryption::register` delegates to `Decryption::register`, which contains:

```rust
// crypto/dkg/pedpop/src/encryption.rs
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

`EncryptionKeyMessage::read` (`encryption.rs:57-59`) accepts arbitrary bytes via `M::read` + `C::read_G`, so an attacker-controlled message reaches this function without authentication beyond the channel assumption the crate explicitly disclaims ("This still doesn't mean the DKG offers an authenticated channel", `encryption.rs:300`). The `HashMap::contains_key` check is performed with `assert!`, producing a process abort rather than an `io::Error`/`DkgError`. Every other malformed-message path in this crate (`EncryptedMessage::read`, `decrypt_with_proof`, `Commitments::read`, `ThresholdKeys::read` → `DkgError`) returns a typed error; this one unconditionally panics.

The surrounding code acknowledges the duplicate-message scenario as an attacker possibility: `Commitments`' doc comment (`crypto/dkg/pedpop/src/lib.rs:98-101`) states a participant sending multiple messages "are faulty and should be presumed malicious" and admits "this library does not handle networking, it is unable to detect if any participant is so faulty." `Decryption` *does* detect the fault — but responds by aborting the victim's process instead of exposing blame, which is precisely the JasPer bug shape (reachable abort on a malicious-input condition).

### Impact Explanation

A single malicious DKG participant (or anyone able to replay/inject a second `EncryptionKeyMessage` under an existing `Participant` index) can unconditionally panic the victim's key-generation process. In a validator/processor deployment this aborts the DKG ceremony and, depending on panic handling, crashes the node — a remote denial of service requiring no threshold collusion, no valid keys, and no race. Repeated submissions allow keeping the set permanently unable to complete key generation, halting all downstream signing.

### Likelihood Explanation

The trigger is deterministic and requires only sending a validly-serialized `EncryptionKeyMessage` twice (or once via a replayed/duplicated transport delivery, since the library delegates dedup to the caller yet still panics internally). No cryptographic work, timing, or privileged position is needed — only participation in the DKG or the ability to inject messages into it.

### Recommendation

Replace the `assert!` with a fallible check returning a typed error (e.g. extend `DkgError`/a `PedPopError` with a `DuplicateRegistration(Participant)` variant) so the caller can blame/exclude the faulty participant. If a defensive assert is retained for internal invariants, additionally expose a `is_registered(participant) -> bool` query and document that callers must dedup before calling `register`.

### Proof of Concept

```rust
// Against crypto/dkg/pedpop, participant P is already registered.
let msg_bytes = malicious_encryption_key_message.serialize(); // valid wire encoding
let msg = EncryptionKeyMessage::<C, M>::read(&mut msg_bytes.as_slice(), params).unwrap();

// First delivery succeeds:
encryption.register(p, msg.clone());

// Attacker replays/duplicates the same (or a second) registration under `p`.
// Decryption::register hits:
//   assert!(!self.enc_keys.contains_key(&participant))
// -> panic!("Re-registering encryption key for a participant") -> process abort.
encryption.register(p, msg);
```

The panic occurs inside `Decryption::register` at `crypto/dkg/pedpop/src/encryption.rs:356-359` before `msg` is consumed, on a condition dictated entirely by attacker-sent bytes.

Caveat: whether a concrete integration delivers a second registration for the same `Participant` depends on the caller's message handling, but the abort lives in in-scope library code and is triggered solely by untrusted input ordering — matching the reachable-abort DoS class of CVE-2018-9154.