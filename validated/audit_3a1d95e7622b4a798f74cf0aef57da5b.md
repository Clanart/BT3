### Title
Unauthenticated panic via out-of-range `recipient` index in PedPoP blame evaluation — (`crypto/dkg/pedpop/src/encryption.rs`)

### Summary
Analogous to CVE-2026-43678 (an unauthenticated peer crashing a process with a single crafted message), `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` with the caller-supplied `recipient` Participant index before any check that the index was registered. Any public caller of `BlameMachine::blame` / `AdditionalBlameMachine::blame` — explicitly documented as usable by parties who were *not* members of the DKG — can pass a `recipient` outside `1 ..= n` together with a self-signed `EncryptedMessage` and crash the evaluating process with a single small message.

### Finding Description
`AdditionalBlameMachine::new` registers encryption keys only for `Participant` indexes `1 ..= n` (`crypto/dkg/pedpop/src/lib.rs:656-660`). Later, `blame` / `blame_internal` forwards the caller-chosen `recipient` into `Decryption::decrypt_with_proof` (`crypto/dkg/pedpop/src/lib.rs:582`). Inside `decrypt_with_proof`, the proof branch builds the DLEq statement by indexing the map with the raw key:

```rust
// crypto/dkg/pedpop/src/encryption.rs
proof
  .dleq
  .verify(
    &mut encryption_key_transcript(self.context),
    &[C::generator(), msg.key],
    &[self.enc_keys[&decryptor], *proof.key],   // panics if decryptor not in 1..=n
  )
```

`HashMap`'s `Index` impl panics on a missing key, so a `decryptor` of `Participant(n + 1)` (or any unregistered index) aborts the thread instead of returning `DecryptionError::InvalidProof`. The only gate before this point is `msg.pop.verify` (`encryption.rs:374-378`), which is satisfiable by the attacker: `pop_challenge` binds `context`, `nonce`, `key`, `sender`, and `msg`, all of which are attacker-chosen fields, so the attacker simply generates their own `key = k·G`, signs the PoP with `k`, and produces a valid `EncryptedMessage` via `EncryptedMessage::read` over their own bytes (`encryption.rs:170-177`). The `EncryptionKeyProof` itself is deserialized unchecked (`EncryptionKeyProof::read`, `encryption.rs:267-269`) and is never validated before the panic site — `*proof.key` is just passed through.

Notably, the panic precedes the DLEq check entirely, so even a garbage `dleq` field works. `AdditionalBlameMachine` exists precisely so *non-participants* can adjudicate blame (`lib.rs:639-648`), meaning no DKG membership or collusion is required — only the ability to submit a blame accusation containing attacker-controlled `sender`/`recipient`/`msg`/`proof`, all of which are ordinary public inputs.

A related site, `self.commitments[&sender]` in `blame_internal` (`crypto/dkg/pedpop/src/lib.rs:599`), has the same shape but is only reached after a *successful* `decrypt_with_proof`, so the `decryptor` path is the clean reachable panic.

### Impact Explanation
A single crafted blame accusation deterministically panics the evaluating process (a blame arbiter, or a participant's `BlameMachine`), killing all in-flight work until restart — the same availability impact class as the reference advisory. Beyond availability, it lets an attacker abort blame adjudication selectively, blocking the protocol's fault-attribution mechanism.

### Likelihood Explanation
Reachable by any unprivileged party able to submit a blame message: construct `EncryptedMessage` with a self-signed PoP (fully attacker-controlled fields), pair it with any `EncryptionKeyProof` encoding, and set `recipient` to an index outside `1 ..= n`. The PoP verification passes by construction, and the panic triggers unconditionally before proof verification. No DKG participation, valid shares, or honest-party cooperation is needed.

### Recommendation
Replace indexing with fallible lookups in `Decryption::decrypt_with_proof` (`self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)`) and in `blame_internal` (`self.commitments.get(&sender)`, returning `sender`/`recipient` or a dedicated error on absence). Also validate `sender`/`recipient` membership against `1 ..= n` at the top of `BlameMachine::blame` and `AdditionalBlameMachine::blame`.

### Proof of Concept
```rust
// Attacker is not a DKG participant. n = 3.
let mut rng = OsRng;
// Build a syntactically valid EncryptedMessage with a PoP we control.
let k = Zeroizing::new(<Secp256k1 as Ciphersuite>::random_nonzero_F(&mut rng));
let pub_key = Secp256k1::generator() * k.deref();
let nonce = Zeroizing::new(<Secp256k1 as Ciphersuite>::random_nonzero_F(&mut rng));
let pub_nonce = Secp256k1::generator() * nonce.deref();
let sender = Participant::new(1).unwrap();
let msg_bytes = vec![0u8; 32]; // any bytes
let pop = SchnorrSignature::<Secp256k1>::sign(
  &k, nonce,
  pop_challenge(context, pub_nonce, pub_key, sender, &msg_bytes), // recomputed by attacker
);
let enc_msg = EncryptedMessage { key: pub_key, pop, msg: SecretShare(...) }; // via ::read of crafted bytes
let proof = EncryptionKeyProof::read(&mut arbitrary_bytes.as_ref()).unwrap();

// recipient = 4 was never registered in enc_keys (only 1..=3)
arbiter.blame(sender, Participant::new(4).unwrap(), enc_msg, Some(proof));
// -> thread panic: HashMap index out of bounds at encryption.rs:388
```