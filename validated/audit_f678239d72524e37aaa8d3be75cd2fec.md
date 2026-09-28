### Title
Panic on unregistered `Participant` index in PedPoP blame decryption enables remote crash of key-gen blame handling - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary

CVE-2024-21165 is a remotely-triggerable denial of service (hang/crash) reachable by a network attacker. The direct analog in Serai is an unconditional Rust panic (`HashMap` index on a missing key) in `Decryption::decrypt_with_proof`, reachable when `AdditionalBlameMachine::blame` is invoked with an accuser `Participant` index that is syntactically valid but was never registered in `enc_keys`. Because the panic aborts the thread rather than returning a `DecryptionError`, a single crafted blame request crashes the victim's key-generation handling.

### Finding Description

`Decryption::decrypt_with_proof` verifies an accuser-supplied `EncryptionKeyProof` by looking up the decryptor's registered encryption key via direct `HashMap` indexing: [1](#0-0) 

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

`self.enc_keys` only contains entries for participants that were previously `register`ed (at most `n` entries, inserted in `register` at crypto/dkg/pedpop/src/encryption.rs:351-362). `decryptor` here is the accuser index. Nothing in `decrypt_with_proof` or `Decryption::register` bounds or membership-checks `decryptor` before the indexing expression `self.enc_keys[&decryptor]` at line 388. In Rust, indexing a `HashMap` with an absent key panics.

The downstream caller confirms the attack surface: the `VerifyBlame` handler in `processor/src/key_gen.rs` takes `accuser` and `accused` directly from a `CoordinatorMessage`, reads the accused's `EncryptedMessage` and the accuser's `EncryptionKeyProof` from untrusted bytes, and calls `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` which routes into `decrypt_with_proof` [2](#0-1) . All other failure modes in this path return `ProcessorMessage::Blame`, but a missing `enc_keys` entry instead panics.

Notably, the codebase itself documents awareness of malformed-input DoS in this exact handler ("a potential DoS attack" comment for trailing commitment bytes at key_gen.rs:276-280), yet the panic path below it is unguarded.

### Impact Explanation

A panic in library code that is documented to not panic (see the comment in `Curve::hash_binding_factor`, crypto/frost/src/curve/mod.rs:86-88: "this library is expected not to panic") crosses the FFI/task boundary and crashes the processor's message handling. An attacker (or a misbehaving relay) who can cause a `VerifyBlame` message to be delivered with an accuser index that has no registered encryption key — e.g., a valid `Participant` value `> n`, or any participant whose `EncryptionKeyMessage` was never registered — causes a repeatable, deterministic crash of the victim's key-gen/blame handling. This matches the CVE class: unauthorized ability to cause a repeatable crash (complete DoS of the affected component) via network-supplied input.

### Likelihood Explanation

Reachability requires a blame message naming an accuser whose index is absent from `enc_keys`. `Participant::new` rejects index 0, but any nonzero `u16` is a valid `Participant`, and `enc_keys` holds at most `n` entries — so `accuser = Participant(n+1)` (or any non-registered index) passes type-level validation and hits the panic. Whether a wrapper layer bounds-checks `accuser` before relaying determines practical likelihood; the crypto crate itself performs no such check and exposes the panic to any caller that forwards network-derived participant indexes. I was unable to fully verify whether an outer coordinator layer filters out-of-range accuser indexes before constructing `VerifyBlame`; if it does not, likelihood is high for anyone able to submit blame claims.

### Recommendation

Replace the panic-capable indexing with a fallible lookup in `Decryption::decrypt_with_proof`:

```rust
let Some(decryptor_key) = self.enc_keys.get(&decryptor).copied() else {
  return Err(DecryptionError::InvalidProof);
};
...
&[decryptor_key, *proof.key],
```

Similarly, `Encryption::encrypt` uses `self.decryption.enc_keys[&participant]` (encryption.rs:466) which panics on unregistered recipients; while that's a local caller bug rather than remotely reachable, converting it to return a `Result` would harden the API. Callers should also validate that `accuser`/`accused` indexes are `<= params.n()` before invoking `blame`.

### Proof of Concept

Conceptual, at the library level (pedpop is in-scope):

```rust
// Setup: a Decryption box with n participants registered (1..=n)
let mut dec = Decryption::<C>::new(context);
for i in 1..=n {
  dec.register(Participant::new(i).unwrap(), key_msg_i);
}

// Attacker submits a blame claim with accuser index n+1 (valid Participant,
// unregistered). dec.enc_keys has no entry for it.
let accuser = Participant::new(n + 1).unwrap();

// Any EncryptedMessage with a *valid* PoP signature so execution reaches
// the indexing line (a legitimate message suffices).
let msg: EncryptedMessage<C, SecretShare<C::F>> = /* validly parsed */;
let proof: EncryptionKeyProof<C> = EncryptionKeyProof::read(&mut bytes).unwrap();

// Panics at crypto/dkg/pedpop/src/encryption.rs:388:
// "no entry found for key" — thread aborts instead of returning InvalidProof.
let _ = dec.decrypt_with_proof(accused, accuser, msg, Some(proof));
```

The panic is deterministic and repeatable: every blame request naming an unregistered accuser crashes the handling thread, yielding complete denial of service of the blame/key-gen path.

### Citations

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

**File:** processor/src/key_gen.rs (L504-556)
```rust
      CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame } => {
        let params = ParamsDb::get(txn, &id.session, id.attempt).unwrap().0;

        let mut share_ref = share.as_slice();
        let Ok(substrate_share) = EncryptedMessage::<
          Ristretto,
          SecretShare<<Ristretto as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        let Ok(network_share) = EncryptedMessage::<
          N::Curve,
          SecretShare<<N::Curve as Ciphersuite>::F>,
        >::read(&mut share_ref, params) else {
          return ProcessorMessage::Blame { id, participant: accused };
        };
        if !share_ref.is_empty() {
          return ProcessorMessage::Blame { id, participant: accused };
        }

        let mut substrate_commitment_msgs = HashMap::new();
        let mut network_commitment_msgs = HashMap::new();
        let commitments = CommitmentsDb::get(txn, &id).unwrap();
        for (i, commitments) in commitments {
          let mut commitments = commitments.as_slice();
          substrate_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
          network_commitment_msgs
            .insert(i, EncryptionKeyMessage::<_, _>::read(&mut commitments, params).unwrap());
        }

        // There is a mild DoS here where someone with a valid blame bloats it to the maximum size
        // Given the ambiguity, and limited potential to DoS (this being called means *someone* is
        // getting fatally slashed) voids the need to ensure blame is minimal
        let substrate_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());
        let network_blame =
          blame.clone().and_then(|blame| EncryptionKeyProof::read(&mut blame.as_slice()).ok());

        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
        let network_blame = AdditionalBlameMachine::new(
          context(&id, NETWORK_KEY_CONTEXT),
          params.n(),
          network_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, network_share, network_blame);
```
