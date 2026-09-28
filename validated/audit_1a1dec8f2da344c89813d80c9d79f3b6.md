### Title
Duplicate DKG encryption-key registration aborts the process via `assert!` instead of returning an error - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::register` enforces "one encryption-key message per participant" with `assert!`, so a malicious DKG participant who submits a second `EncryptionKeyMessage` for the same `Participant` index causes the honest node's process to panic/abort. This is the direct analog of CVE-2019-15604: improper handling of crafted input (there a malformed X.509 certificate, here a duplicated PedPoP encryption-key message) causing a process abort rather than a recoverable error.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, the `Decryption` box tracks per-participant encryption public keys in `enc_keys: HashMap<Participant, C::G>`. `register` is invoked once per received `EncryptionKeyMessage` (parsed from untrusted bytes via `EncryptionKeyMessage::read`, which accepts any canonical `C::G` as `enc_key`):

```rust
// crypto/dkg/pedpop/src/encryption.rs:351-362
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

Elsewhere in the same crate, malformed peer input is handled with `io::Error` (e.g., `Commitments::read` returning errors on invalid points in `crypto/dkg/pedpop/src/lib.rs:110-128`) or a typed `DecryptionError` (`encryption.rs:332-338`). The `assert!` is the only peer-reachable path that aborts. A faulty DKG participant controls which `Participant` index their messages are processed under and can simply send two `EncryptionKeyMessage`s; the second `register` call panics. Note the sibling documentation at `crypto/dkg/pedpop/src/lib.rs:98-101` explicitly says duplicate commitments make a participant "faulty and should be presumed malicious" — i.e., duplicate messages from a peer are an expected adversarial input, yet here they are answered with a process abort.

I could not fully verify the caller's deduplication behavior (the callers live in `crypto/dkg`/`PedPoP` driving code), but nothing in `register` or `EncryptedMessage`/`EncryptionKeyMessage::read` prevents a second message for the same participant from reaching this `assert!`, and the crate's own comments acknowledge that duplicate/forged messages are the caller-visible adversarial case.

### Impact Explanation
An unprivileged participant in a DKG/PedPoP session can abort the victim's process mid-protocol by sending a duplicate encryption-key registration. This is a remote, input-triggered denial of service (availability impact only, matching the CVE's `A:H` / 7.5 shape): the node crashes instead of marking the participant faulty, potentially halting key generation and requiring manual restart/retry of the ceremony.

### Likelihood Explanation
A participant only needs to emit two `EncryptionKeyMessage`s claiming the same sender index during the key-registration phase — a trivially constructible input with no cryptographic work required. Exploitability depends on the surrounding protocol delivering a second message for the same participant to `register`; the library assumes the caller enforces deduplication but encodes the assumption as a hard `assert!` rather than an error, so any caller that forwards peer messages naively is vulnerable.

### Recommendation
Replace the `assert!` with a fallible check: change `Decryption::register`/`Encryption::register` to return `io::Result<M>` (or a `RegistrationError::DuplicateKey(Participant)`) so a duplicate registration is surfaced as participant misbehavior and handled by the blame/fault path like other malformed inputs. Alternatively, keep the signature and return the existing entry unmodified while flagging the participant as faulty. The panic should be reserved for genuine programmer error, not peer-controlled duplication.

### Proof of Concept
```rust
// Honest node processing two EncryptionKeyMessage<C, M> values that a malicious
// peer sent, both claiming Participant(1):
let mut decryption = Decryption::<C>::new(context);

let msg1 = EncryptionKeyMessage::read(&mut bytes1, params)?; // peer-controlled bytes
let msg2 = EncryptionKeyMessage::read(&mut bytes2, params)?; // second message, same index

decryption.register(Participant(1), msg1); // ok
decryption.register(Participant(1), msg2); // assert! fires -> process aborts
```
`msg2` requires no valid proof or key knowledge: `enc_key` is any canonical `C::G` accepted by `EncryptionKeyMessage::read`, so the malicious participant needs only to duplicate its own (or relay a second) registration message to crash the honest node at `crypto/dkg/pedpop/src/encryption.rs:356-359`.