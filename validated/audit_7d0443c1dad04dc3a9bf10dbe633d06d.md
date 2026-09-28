### Title
Untrusted DKG/blame messages panic the host via `Decryption::register` assert and `enc_keys` map indexing - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The CKB advisory is a denial-of-service bug class: an unprivileged remote party supplies crafted, semantically-valid-but-unexpected input and the victim process panics. The analogous shape in Serai lives in PedPoP's encryption book-keeping: `Decryption::register` hard-asserts on duplicate registration, and `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` without checking presence. Both are reachable from deserialized peer messages (`EncryptionKeyMessage::read` / `EncryptedMessage::read` / `EncryptionKeyProof::read`) and abort the process instead of returning an error.

### Finding Description
`Decryption::register` panics on a second registration for the same `Participant`:

```rust
// crypto/dkg/pedpop/src/encryption.rs:352-361
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

The `EncryptionKeyMessage` fed to it is fully attacker-controlled bytes (`EncryptionKeyMessage::read` at line 57-59 reads `M::read` plus a `C::read_G`). The `Commitments` documentation (crypto/dkg/pedpop/src/lib.rs:96-101) explicitly states the library "does not handle networking" and cannot detect a participant sending multiple sets of commitments — yet the duplicate-delivery case is enforced with `assert!` rather than an error return, so a participant that delivers its key-registration message twice (or a caller that dedupes on commitments but not on key messages) panics the victim.

`decrypt_with_proof` has the same defect in the blame path:

```rust
// crypto/dkg/pedpop/src/encryption.rs:381-392
proof
  .dleq
  .verify(
    &mut encryption_key_transcript(self.context),
    &[C::generator(), msg.key],
    &[self.enc_keys[&decryptor], *proof.key],
  )
```

`self.enc_keys[&decryptor]` panics if `decryptor` was never registered. `from`, `decryptor`, `msg`, and `proof` are all derived from peer-supplied data during blame resolution; a blame flow naming a participant index that never completed registration hits a HashMap indexing panic.

A third instance: `Encryption::encrypt` (line 466) uses `self.decryption.enc_keys[&participant]`, panicking when generating a share for a participant index with no registered key.

### Impact Explanation
Any of these panics aborts the host thread/process mid-DKG or mid-blame-resolution. For a node embedding this DKG (e.g., a Serai validator handling tributary-federated key generation), a single duplicate or misdirected PedPoP message causes a crash — the same availability impact as the CKB panic, where two crafted inputs took down nodes. Severity: Medium (DoS only, no secret leakage; requires the caller to feed the second/misrouted message into the machine).

### Likelihood Explanation
Reachability requires the host to invoke `register`/`decrypt_with_proof`/`encrypt` with peer-derived `Participant` indexes — which is the intended usage of these crate-internal functions (they are called from `PedPop`/`PedPoP` machine logic driven by received `EncryptionKeyMessage`s). The `assert!` fires on a plain duplicate delivery; the map-index panics fire whenever a `decryptor`/`participant` index has no entry — e.g., a participant that sent commitments but a malformed or missing key message, or a blame proof referencing a non-registered index. No threshold collusion or key compromise is needed — just message delivery the library itself acknowledges it cannot police.

### Recommendation
Replace panicking paths with error returns:
- In `Decryption::register`, return `io::Result`/a `DkgError` variant on duplicate instead of `assert!`.
- In `decrypt_with_proof` and `Encryption::encrypt`, use `enc_keys.get(&decryptor)` / `.get(&participant)` and surface a `DecryptionError`/`DkgError` rather than indexing.

### Proof of Concept
```rust
// Conceptual; within crypto/dkg/pedpop tests context.
// 1. Honest setup: Encryption::new(context, i, rng) for the victim.
// 2. Attacker sends EncryptionKeyMessage::<C, M>::read(attacker_bytes) -> msg.
// 3. Victim calls encryption.register(attacker_idx, msg) — succeeds.
// 4. Attacker re-sends the same (or a second) EncryptionKeyMessage.
// 5. Victim calls encryption.register(attacker_idx, msg2)
//    -> assert! fires at crypto/dkg/pedpop/src/encryption.rs:356-359. Process aborts.
//
// Variant B: victim processes a blame flow calling
// Decryption::decrypt_with_proof(from, decryptor, msg, Some(proof))
// where `decryptor` never registered a key
//    -> HashMap index panic at encryption.rs:388.
```

Caveats I could not fully verify within scope: these functions are `pub(crate)`, so exact exposure depends on the parent `PedPoP` machine's message loop (crypto/dkg/pedpop/src/lib.rs); if the caller rigorously deduplicates before calling `register`, the assert is unreachable and the finding reduces to the `decrypt_with_proof`/`encrypt` indexing paths. Other inspected panic sites — `Interpolation::Constant` indexing (`crypto/dkg/src/lib.rs:228`), `denom.invert().unwrap()` (`:246`), `BatchVerifier::blame_vartime().unwrap()` (`crypto/multiexp/src/batch.rs:131,141`), `BindingFactor` lookups (`crypto/frost/src/nonce.rs:176,206`), and `weight()` slicing (`crypto/schnorr/src/aggregate.rs:54`) — are all guarded by validated lengths/sets and did not yield a reachable panic.