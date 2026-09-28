### Title
Unvalidated participant indexes in blame resolution cause a panic (denial of service) via an attacker-crafted blame statement - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept arbitrary `sender` and `recipient` `Participant` values supplied alongside an untrusted `EncryptedMessage`/`EncryptionKeyProof`. Neither value is bounds-checked before being used as a `HashMap` key. Passing a `recipient` whose encryption key was never registered (any index `> n`, or the local participant `i`, whose key is deliberately never inserted) makes `Decryption::decrypt_with_proof` evaluate `self.enc_keys[&decryptor]`, which panics. Likewise, a `sender` index absent from `self.commitments` panics in `blame_internal`. Any peer can crash a participant evaluating blame by naming an out-of-range participant — the analog of CVE-2017-8820's "malformed input crashes the node".

### Finding Description
`blame` forwards its caller-supplied `sender`/`recipient` directly into `blame_internal` (crypto/dkg/pedpop/src/lib.rs:575-609) and `AdditionalBlameMachine::blame` (lib.rs:674-682) with no check that either is a valid participant for the session. `blame_internal` calls `Decryption::decrypt_with_proof`, which, once the message's proof-of-possession verifies (trivially satisfiable — the attacker constructs the `EncryptedMessage` themselves and knows the ephemeral key), evaluates `self.enc_keys[&decryptor]` (crypto/dkg/pedpop/src/encryption.rs:388). `enc_keys` is only populated for registered participants: in the normal flow `Decryption::register` is invoked for each `l != params.i()` (lib.rs:313-315), so `enc_keys` never contains the local index `i` nor any `Participant > n`. `Participant::new` only rejects zero, so any u16 in `1..=u16::MAX` is accepted at deserialization boundaries. Indexing a missing key panics. The same unchecked indexing exists at `self.commitments[&sender]` (lib.rs:599) when `sender` isn't a session participant. There is no validation such as `u16::from(recipient) <= n` anywhere on this path, unlike the explicit checks in `sign`/`view`/`ThresholdKeys::new`.

### Impact Explanation
A single malformed blame accusation crashes the process evaluating it (`panic!` on a `HashMap` index, unwinding/aborting the thread). In a validator/coordinator context where blame statements are relayed for arbitration, one malicious or malfunctioning participant can remotely DoS every node that evaluates the claim — analogous to CVE-2017-8820, where a malformed descriptor crashed Tor directory authorities (availability-only impact, no secret disclosure).

### Likelihood Explanation
Triggering requires only that an unprivileged party cause a blame evaluation with a crafted `(sender, recipient)` pair — both are plain `Participant` values the attacker fully controls in the accusation message, and the accompanying `EncryptedMessage` passes the PoP check because the attacker generates its encryption key. No threshold collusion, valid shares, or cryptographic breaks are needed; it is a simple out-of-range index on an untrusted input.

### Recommendation
Validate `sender` and `recipient` at the top of `blame_internal`/`AdditionalBlameMachine::blame` (e.g., `u16::from(x) <= n`, `sender != recipient`, both registered), returning a defined error or a default faulty party instead of indexing. Replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `.get()` + error, matching the explicit participant validation used in `ThresholdKeys::view` (crypto/dkg/src/lib.rs:463-491) and `AlgorithmSignMachine::sign` (crypto/frost/src/sign.rs:297-310).

### Proof of Concept
```rust
// crypto/dkg/pedpop/src/tests.rs style harness, C = Ristretto, n = 2, t = 2
let params = ThresholdParams::new(2, 2, Participant::new(1).unwrap()).unwrap();
// ... run KeyGenMachine -> generate_secret_shares -> calculate_share to obtain BlameMachine ...
let machine: BlameMachine<Ristretto> = /* completed calculate_share */;

// Attacker-controlled accusation: recipient = 3 is out of range for n = 2.
// `msg` is a well-formed EncryptedMessage the attacker produced with their own
// ephemeral key, so `msg.pop.verify(...)` succeeds and execution reaches
// `self.enc_keys[&decryptor]` (encryption.rs:388).
let bad_recipient = Participant::new(3).unwrap();
machine.blame(
  Participant::new(2).unwrap(), // sender
  bad_recipient,                // recipient: not a session participant
  attacker_msg,                 // EncryptedMessage::read output, valid PoP
  Some(attacker_proof),         // any EncryptionKeyProof::read output
);
// Panics: "no entry found for key" inside HashMap index at
// crypto/dkg/pedpop/src/encryption.rs:388 -> process/thread crash.
```
The same panic is reachable via `AdditionalBlameMachine::blame` (lib.rs:674), used to arbitrate third-party accusations, and via `sender` indexes absent from `self.commitments` (lib.rs:599).