### Title
Unbounded HashMap indexing on an attacker-supplied `Participant` causes a repeatable panic (DoS) during blame/decryption verification - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` without checking that `decryptor` is a registered participant. `enc_keys` is only populated for participants `1..=n` during `verify_r1`, but `decryptor` is derived from attacker-controlled protocol bytes (`Participant::new(u16)` accepts any non-zero `u16` up to 65535, unbounded by `n`). Passing an in-range-but-unregistered index (e.g. `Participant(5000)` with `n = 150`) panics the node processing the blame message — a hang/crash-class denial of service matching CVE-2020-2768's impact profile (low-privileged network attacker causing a repeatable crash of a distributed system component).

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `decrypt_with_proof` is the routine used to verify a blame disclosure: an accuser claims an `EncryptedMessage` was faulty, publishes/receives an `EncryptionKeyProof`, and honest parties re-decrypt the message to assign blame. The verification path is:

```rust
// crypto/dkg/pedpop/src/encryption.rs ~L381-392
if let Some(proof) = proof {
  proof
    .dleq
    .verify(
      &mut encryption_key_transcript(self.context),
      &[C::generator(), msg.key],
      &[self.enc_keys[&decryptor], *proof.key],   // <-- panics on unregistered key
    )
    .map_err(|_| DecryptionError::InvalidProof)?;
```

`self.enc_keys` is a `HashMap<Participant, C::G>` populated exclusively in `Decryption::register`, which is only invoked from `SecretShareMachine::verify_r1` (`crypto/dkg/pedpop/src/lib.rs` ~L313-315) for `self.params.all_participant_indexes()` — i.e., exactly the keys `1..=n`. The `Participant` type (`crypto/dkg/src/lib.rs` L29-35) enforces only non-zero; any `u16` in `1..=65535` is a valid `Participant`, including values `> n`.

The `decryptor`/`accuser`/`faulty` indices in DKG blame traffic are deserialized from untrusted bytes as `Participant::new(u16)` (see `coordinator/src/tributary/transaction.rs` L346-353, which reads `accuser` and `faulty` with no bound against `n`). A participant index such as `Participant::new(4000)` in a session with `n = 150` reaches `enc_keys[&decryptor]` on a key that was never inserted, and `HashMap`'s `Index` impl panics.

Additionally, `Decryption::register` itself contains an `assert!(!self.enc_keys.contains_key(&participant), "Re-registering encryption key for a participant")` (encryption.rs ~L356-359). While the `HashMap`-keyed `commitment_msgs` in `verify_r1` prevents duplicate insertion in the normal flow, any caller that routes a second registration for the same participant (e.g., a second commitments message that a networking layer fails to dedup, which the `Commitments` docs note is explicitly the caller's responsibility) panics — the code acknowledges the library "is unable to detect" duplicate commitment sets, yet uses a hard `assert!` instead of an error, converting a documented caller-facing condition into a crash.

### Impact Explanation
Successful exploitation results in a guaranteed, repeatable panic in every honest party that verifies the malicious blame/decryption message. In an async/embedded context this aborts the DKG session and may crash the processor/coordinator task handling it (Rust panics in non-`catch_unwind` tasks tear down the signer/validator worker). This matches the CVE-2020-2768 bug class: an unprivileged protocol participant with network access supplies malformed-but-parseable messages that cause a "frequently repeatable crash (complete DOS)". Integrity impact is secondary — the panic aborts key generation progress — but availability loss is total for the handling task.

### Likelihood Explanation
The trigger is cheap: one `Participant` field set to a value `> n` in a blame/verification message. No cryptographic work, no collisions, no valid shares needed — the panic occurs *before* the DLEq verification result can even be evaluated, on the `enc_keys[&decryptor]` lookup itself. The only mitigating factor is whether the enclosing application bounds participant indexes before calling into `BlameMachine`/`Decryption`; the in-scope crate itself performs no such bound, and `Participant` carries no `n` context.

### Recommendation
Replace indexing with a fallible lookup in `decrypt_with_proof`:

```rust
let enc_key = self
  .enc_keys
  .get(&decryptor)
  .ok_or(DecryptionError::InvalidProof)?;
```

Add a dedicated `DecryptionError::UnknownParticipant` variant if distinct blame treatment is desired. In `Decryption::register`, replace `assert!(!self.enc_keys.contains_key(&participant))` with a returned error so duplicate-registrations from any caller become faults rather than panics. In `BlameMachine`/blame entry points, validate `accuser` and `accused` against `params.all_participant_indexes()` (or `u16::from(p) <= params.n()`) before touching `enc_keys`, mirroring the `InvalidParticipant` checks already used by `ThresholdKeys::view`.

### Proof of Concept
Conceptual (library-level, no network harness needed):

```rust
// Setup: ThresholdParams::new(t, n, i) with n = 3, all participants
// complete round 1 so Decryption.enc_keys = {1, 2, 3}.
let mut decryption: Decryption<Ristretto> = /* from KeyMachine::into_decryption() */;

// Attacker crafts a blame message naming a decryptor outside 1..=n
let fake_accuser = Participant::new(4000).unwrap(); // valid Participant, > n

// Any EncryptedMessage with a valid pop; or short-circuit:
// enc_keys[&fake_accuser] executes unconditionally inside `if let Some(proof)`
let msg = EncryptedMessage::<Ristretto, SecretShare<F>>::read(&mut crafted, params)?;
let proof = EncryptionKeyProof::<Ristretto>::read(&mut crafted_proof)?;

// This line panics: `enc_keys[&fake_accuser]` — HashMap index on absent key
let _ = decryption.decrypt_with_proof(sender, fake_accuser, msg, Some(proof));
// thread '<unnamed>' panicked: 'no entry found for key'
```

The panic fires regardless of whether the embedded `EncryptedMessage`/`EncryptionKeyProof` are otherwise valid, because the offending `enc_keys[&decryptor]` lookup is evaluated while constructing the `dleq.verify` arguments.

Caveat: I verified the panic site and the fact that `enc_keys` only holds `1..=n`, and that `Participant` fields in blame traffic are read as unbounded non-zero `u16`s. I did not trace every intermediate wrapper between wire deserialization and `BlameMachine::blame`, so if every caller pre-validates `accuser <= n`, reachability narrows — but the in-scope API itself exposes the crash unconditionally and performs no such validation.