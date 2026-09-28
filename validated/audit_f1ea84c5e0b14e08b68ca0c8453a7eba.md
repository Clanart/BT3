### Title
Invalid PedPoP ciphertext leaks the recipient’s ECDH key before proof verification - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` decrypts each submitted `EncryptedMessage` and immediately rejects a non-canonical secret-share encoding. The returned blame value contains an `EncryptionKeyProof` with the raw ECDH key, even though the sender’s proof-of-possession for the ephemeral key was only queued for batch verification and has not yet succeeded.

Because `Encryption::decrypt` calculates and returns `key = enc_key * msg.key` unconditionally, a malicious participant can copy the ephemeral key from another encrypted share, attach a ciphertext that decodes to an invalid scalar, and receive the corresponding ECDH key in the blame proof. That key can then be used to decrypt the copied sender’s ciphertext, revealing that participant’s secret share.

### Finding Description
`Encryption::decrypt` queues `msg.pop` into the supplied `BatchVerifier`, calculates the ECDH key, decrypts the ciphertext, and returns both the plaintext and an `EncryptionKeyProof` containing the raw ECDH key. The proof-of-possession is therefore deferred to batch verification rather than checked before the ECDH key is exposed. [1](#0-0) 

`KeyMachine::calculate_share` processes each submitted encrypted share. If the decrypted `SecretShare` bytes are not a canonical scalar, it returns `PedPoPError::InvalidShare` immediately with `blame: Some(blame.clone())`. This early return bypasses the later `batch.verify_with_vartime_blame()` call, so the queued PoP statement is never verified. [2](#0-1) 

The leaked `EncryptionKeyProof` includes both the ECDH key and a DLEq proof tying it to the victim’s registered encryption key and the attacker-selected `msg.key`. [3](#0-2) 

### Impact Explanation
An attacker can turn the PedPoP participant into a static-ECDH decryption oracle:

1. Observe a legitimate encrypted share containing an ephemeral public key `msg.key`.
2. Submit a malicious `EncryptedMessage` under their own participant ID with:
   - the same `msg.key`,
   - an invalid proof-of-possession,
   - ciphertext bytes that decrypt to a non-canonical scalar.
3. `Encryption::decrypt` computes the ECDH key using the victim’s static encryption secret and attacker-selected `msg.key`.
4. `calculate_share` returns before verifying the queued PoP, attaching the `EncryptionKeyProof` containing that ECDH key.
5. The attacker uses the disclosed ECDH key to decrypt the legitimate ciphertext associated with the copied `msg.key`, recovering another participant’s secret share.

Recovery of one or more threshold key shares is a direct compromise of the DKG’s secret material. If the attacker can collect enough shares through repeated observations and oracle queries, they can reconstruct the threshold private key or otherwise violate the protocol’s confidentiality assumptions.

### Likelihood Explanation
The malformed message can be formed entirely from public protocol data: the attacker controls their `EncryptedMessage`, can reuse a visible ephemeral key, and can choose ciphertext bytes that decode to an invalid scalar. The vulnerable path does not require controlling the recipient’s private key, exploiting unsafe code, corrupting memory, or colluding with a threshold of validators.

The necessary condition is that the PedPoP error path returns the generated blame proof before the deferred PoP batch verification runs. The code does exactly that on `C::F::from_repr` failure. [4](#0-3) 

### Recommendation
Do not expose an `EncryptionKeyProof` for a message whose proof-of-possession has not been verified.

Concretely:

- Verify `msg.pop` synchronously inside `Encryption::decrypt`, before calculating or returning the ECDH key; or
- Keep batch verification, but avoid early error returns that carry an `EncryptionKeyProof` until `batch.verify_with_vartime_blame()` has succeeded; and
- On invalid plaintext encoding, return `blame: None` when the associated PoP has not yet been authenticated.

The safest construction is to authenticate `msg.key` first and only derive or disclose the ECDH key after the sender proves control of that ephemeral key.

### Proof of Concept
The vulnerability can be demonstrated by driving `KeyMachine::calculate_share` with a malformed `EncryptedMessage`:

```rust
// Let honest_share be another participant's EncryptedMessage.
// Its msg.key is public, but decrypting its ciphertext requires the ECDH key.

let malicious = EncryptedMessage {
  // Reuse the honest message's ephemeral key.
  key: honest_share.key,

  // Any bytes here are acceptable; the PoP is not checked before the early error.
  pop: invalid_pop,

  // Choose ciphertext so decrypt() yields bytes which C::F::from_repr rejects.
  msg: invalid_scalar_ciphertext,
};

let err = victim_key_machine
  .calculate_share(&mut rng, shares_containing(attacker_i, malicious))
  .unwrap_err();

// For the invalid-scalar path, calculate_share returns:
// PedPoPError::InvalidShare { blame: Some(EncryptionKeyProof { key, .. }) }
// where `key` is ECDH(victim_static_enc_key, honest_share.key).
```

With `key`, the attacker applies the same PedPoP cipher to `honest_share.msg` and obtains the honest participant’s `SecretShare`. The root cause is that the ECDH-bearing blame proof is created before the queued PoP statement is verified and is returned by the early canonical-scalar error path. [5](#0-4) [6](#0-5)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-500)
```rust
  pub(crate) fn decrypt<R: RngCore + CryptoRng, I: Copy + Zeroize, E: Encryptable>(
    &self,
    rng: &mut R,
    batch: &mut BatchVerifier<I, C::G>,
    // Uses a distinct batch ID so if this batch verifier is reused, we know its the PoP aspect
    // which failed, and therefore to use None for the blame
    batch_id: I,
    from: Participant,
    mut msg: EncryptedMessage<C, E>,
  ) -> (Zeroizing<E>, EncryptionKeyProof<C>) {
    msg.pop.batch_verify(
      rng,
      batch,
      batch_id,
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    );

    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
    (
      msg.msg,
      EncryptionKeyProof {
        key,
        dleq: DLEqProof::prove(
          rng,
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &self.enc_key,
        ),
      },
    )
```

**File:** crypto/dkg/pedpop/src/lib.rs (L474-499)
```rust
    let mut batch = BatchVerifier::new(shares.len());
    let mut blames = HashMap::new();
    for (l, share_bytes) in shares.drain() {
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
      *self.secret += share.deref();

      blames.insert(l, blame);
      batch.queue(
        rng,
        BatchId::Share(l),
        share_verification_statements::<C>(self.params.i(), &self.commitments[&l], share),
      );
    }
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```
