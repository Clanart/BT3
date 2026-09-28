### Title
Unvalidated HashMap index on accuser-supplied participant index panics during DKG blame decryption - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` where `decryptor` is a `Participant` value carried in attacker-controlled blame data, without checking membership. This is the Serai analog of CVE-2017-5994: an attacker-controlled index (`num_elements` in virglrenderer, `decryptor` here) reaches an out-of-bounds container access and crashes the process. [1](#0-0) 

### Finding Description
In `decrypt_with_proof`, the DLEq proof binding the revealed ECDH key is verified against `self.enc_keys[&decryptor]`. `enc_keys` is populated only by `register`, which is called once per participant that supplied a valid `EncryptionKeyMessage` in round 1 (`verify_r1` / `Encryption::register`). [2](#0-1) 

A participant who was excluded before registering (or whose index the accuser simply fabricates, since `Participant` here is just a nonzero `u16` read from untrusted bytes) has no entry in `enc_keys`. When a blame/`InvalidDkgShare` message naming such a `decryptor` is processed, `self.enc_keys[&decryptor]` panics via `HashMap`'s `Index` impl — the same failure mode as the virglrenderer OOB array access, just a Rust panic instead of a raw heap read. The sibling call in `Encryption::encrypt` (`self.decryption.enc_keys[&participant]`, line 466) has the same pattern, though it is guarded by `validate_map` earlier in `generate_secret_shares`; `decrypt_with_proof` has no equivalent guard on `decryptor`. [3](#0-2) 

The untrusted input path is `EncryptedMessage::read` / `EncryptionKeyProof::read` feeding `decrypt_with_proof` during share/blame verification. [4](#0-3) 

### Impact Explanation
A single malformed blame message causes a panic in the DKG participant process — denial of service of the key-generation/signing node, matching the CVE's CVSS 5.5 (local party → availability loss). If run inside a coordinator/processor task without panic catching, this aborts the participant's handling of the whole DKG attempt.

### Likelihood Explanation
Requires the attacker to be a DKG participant able to submit a blame/accusation message — the same unprivileged-party reachability class as the CVE's "local guest OS user". Triggering is trivial: name any `decryptor` index that never registered an encryption key (e.g., a dropped/faulty participant or an index not in the final commitment map).

### Recommendation
Replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` in `decrypt_with_proof`, and audit `Encryption::encrypt`'s `enc_keys[&participant]` for the same treatment or assert the `validate_map` precondition in a comment/assert.

### Proof of Concept
```rust
// After a DKG round where `decryptor` P never registered an encryption key:
let blame_msg: EncryptedMessage<C, SecretShare<C::F>> =
    EncryptedMessage::read(&mut attacker_bytes, params)?; // valid pop/key format
let proof = EncryptionKeyProof::read(&mut attacker_proof_bytes)?;
// decryption.enc_keys contains no entry for P
decryption.decrypt_with_proof(accuser, P /* unregistered */, blame_msg, Some(proof));
// panics at self.enc_keys[&P] instead of returning Err(DecryptionError)
```

Caveat: I could not trace the exact upstream call site that passes `decryptor` into `decrypt_with_proof` (the PedPoP blame handling in `crypto/dkg/pedpop/src/lib.rs`), so reachability rests on `decryptor` being sourced from the accusation message rather than strictly `params.i()`; if it is always the local participant, this degrades to non-exploitable.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-177)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-395)
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

      cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
```
