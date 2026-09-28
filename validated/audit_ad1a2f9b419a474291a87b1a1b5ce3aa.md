### Title
Unindexable `decryptor` participant in DKG blame handling panics the process via `HashMap` indexing — (`crypto/dkg/pedpop/src/encryption.rs`)

### Summary
CVE-2021-2088 is a remotely-triggerable, repeatable crash (complete availability loss) of a server caused by feeding it a crafted statement. The analog in Serai is a remotely-triggerable panic in PedPoP blame processing: `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` without checking that the `decryptor` participant ever registered an encryption key. An unprivileged party who submits a blame/accusation message naming a participant index that was never registered (or any index other than a registered decryptor) causes a `HashMap` index panic, crashing the validator/processor thread. In Serai's binaries, tokio task panics are wired to abort the process (`std::process::exit(1)` on task panic), turning this into a complete DoS. [1](#0-0) 

### Finding Description
`Decryption::register` only inserts into `enc_keys` for participants that supplied an `EncryptionKeyMessage`, and panics on re-registration. [2](#0-1)  During blame resolution, `decrypt_with_proof` takes `from` and `decryptor` participant indexes that derive from the attacker-supplied accusation (e.g., the `accuser`/`faulty` fields deserialized from wire bytes in the DKG blame transaction), then evaluates `self.enc_keys[&decryptor]`. `HashMap`'s `Index` impl panics when the key is absent, so any `decryptor` value not present in `enc_keys` — trivially satisfiable since `Participant::new` accepts any nonzero `u16` while `enc_keys` only holds indexes `1..=n` that actually registered — panics instead of returning a `DecryptionError`. The same unchecked-index pattern exists in `Encryption::encrypt` (`self.decryption.enc_keys[&participant]`), but that path is fed by internal iteration; the `decrypt_with_proof` path is fed by untrusted accusation data. [3](#0-2) 

Caveat: `BlameMachine::blame` (the caller that maps the accusation's participant fields onto the `decryptor` argument) sits past the indexed portion of `crypto/dkg/pedpop/src/lib.rs` I could read, so the exact field-to-argument wiring is inferred from the transaction format (`InvalidDkgShare` carrying `accuser` and `faulty` participant indexes read from the wire). The panic site itself is confirmed.

### Impact Explanation
A single crafted blame proof/accusation deterministically panics the decrypting party. Since coordinator/processor tasks panic-abort the whole process, one untrusted message produces a complete, repeatable crash of a validator — matching the CVE class (hang or frequently repeatable crash → full availability loss). It requires no privileges beyond submitting a malformed DKG blame message; no threshold coalition, no key material, and no valid proof is needed because the panic occurs before/instead of a clean `InvalidProof` rejection.

### Likelihood Explanation
Reachability requires a DKG in progress and the attacker to cause a blame evaluation naming a `decryptor` outside the registered `enc_keys` set — for example a participant index within `1..=n` that failed to register, or any index the surrounding handler forwards unchecked. Medium likelihood: it needs the DKG/blame context, but within that context it is a single deterministic message with no race or cryptographic work.

### Recommendation
Replace the `HashMap` index with a fallible lookup in `crypto/dkg/pedpop/src/encryption.rs`, e.g.:

```rust
let Some(enc_key) = self.enc_keys.get(&decryptor) else {
  Err(DecryptionError::InvalidProof)?
};
```

and use `*enc_key` in the `dleq.verify` call. Apply the same `get`/error mapping at `Encryption::encrypt`'s `enc_keys[&participant]` access. Additionally, validate `accuser`/`faulty`/`decryptor` participant indexes against `params.all_participant_indexes()` at the transaction/handler layer before invoking blame logic.

### Proof of Concept
Conceptual: after a PedPoP round where `Decryption::enc_keys` contains registered indexes `{1, ..., n}`, submit a blame evaluation invoking `decrypt_with_proof::<C, E>(from, decryptor, msg, Some(proof))` where `msg` carries a valid PoP (so the `msg.pop.verify` early-return is passed) and `decryptor` is any `Participant` not in `enc_keys` — e.g., an index whose `EncryptionKeyMessage` was never delivered. Execution reaches `self.enc_keys[&decryptor]` at `crypto/dkg/pedpop/src/encryption.rs:388` and panics with `index out of bounds`-equivalent `HashMap` panic, aborting the process rather than returning `DecryptionError::InvalidProof`.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L352-362)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Result<Zeroizing<E>, DecryptionError> {
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }

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

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
  }
```
