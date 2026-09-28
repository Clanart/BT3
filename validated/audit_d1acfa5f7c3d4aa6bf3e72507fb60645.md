### Title
PedPoP DKG accepts identity/arbitrary encryption keys with no proof of possession, enabling decryption of secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2023-1226 (insufficient policy enforcement allowing a security mechanism to be bypassed), the PedPoP DKG registers each participant's ECDH encryption key without enforcing any validity policy: no identity check and no proof of knowledge/possession. A participant can register `enc_key = identity` (or any key whose discrete log is publicly known, e.g. `G * x` for known `x`), causing every secret share encrypted to them to be encrypted under a publicly derivable ChaCha20 key.

### Finding Description
`EncryptionKeyMessage::read` reads `enc_key` with `C::read_G` (the `Ciphersuite` impl at `crypto/ciphersuite/src/lib.rs:91-101`), which enforces canonical encoding but does **not** reject the identity point — unlike `Curve::read_G` in `crypto/frost/src/curve/mod.rs:125-131`, which does. `Decryption::register` then inserts the key unconditionally, and `verify_r1` (`crypto/dkg/pedpop/src/lib.rs:313-331`) validates only the commitments' Schnorr PoK — it performs no check on `msg.enc_key` at all.

When shares are later encrypted, `encrypt` computes `ecdh(&key, to) = to * key` (`encryption.rs:95-97, 154`). If `to` is the identity, the shared secret is the identity point — a constant, publicly known value. `cipher` derives the ChaCha20 key solely from `context || identity.to_bytes()` (`encryption.rs:101-133`), so anyone can reconstruct the keystream and decrypt the `SecretShare` ciphertext. Similarly, an attacker can register `enc_key = G * x` for a known `x`, making the shared key `msg.key * x` computable by any observer of the per-message public key.

### Impact Explanation
The DKG's confidentiality guarantee is that secret shares are only readable by their intended recipient. With an identity or known-DL encryption key, the `EncryptedMessage<C, SecretShare>` ciphertext (which is further exposed to third parties during blame evaluation via `AdditionalBlameMachine`/`blame`) is decryptable by anyone, yielding key share recovery for all shares addressed to the malicious participant — including shares from honest dealers. This breaks the secrecy of the resulting `ThresholdKeys` secret shares of honest participants.

### Likelihood Explanation
The attack requires only that an unprivileged DKG participant submit crafted bytes through `EncryptionKeyMessage::read` — fully public input, no collusion needed. The cost is trivial (encode the identity point or `G*x` in place of a real key). It is deterministic, not probabilistic.

### Recommendation
Enforce policy on registered encryption keys in `verify_r1`/`register`:
- Reject identity `enc_key` (reject `is_identity()` after `C::read_G` in `EncryptionKeyMessage::read`).
- Require a Schnorr proof of knowledge of `enc_key`'s discrete log, bound to `(context, participant)` — mirroring the existing PoK on `commitments[0]` and the per-message PoP on `EncryptedMessage.key` — so the submitter proves they hold a key whose DL is unknown to others.

### Proof of Concept
1. Malicious participant `l` calls `EncryptionKeyMessage::read`-equivalent construction with `enc_key = C::G::identity()` and a valid `Commitments` PoK.
2. `verify_r1` accepts the message: `register` stores `enc_keys[l] = identity` with no check (`encryption.rs:351-362`).
3. Each honest participant `i` encrypts `share_{i→l}` via `encrypt(...)`: `ecdh(&key_i, identity) = identity`, so `cipher(context, identity)` produces a keystream derivable by anyone who knows `context` (public).
4. Anyone obtaining the ciphertext `EncryptedMessage<C, SecretShare>` — e.g., a third party running `AdditionalBlameMachine::new`/`blame` after a fault accusation — applies `cipher(context, identity)` and recovers `share_{i→l}` in the clear, recovering honest participants' secret share contributions. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L313-334)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;
```

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
  }
```
