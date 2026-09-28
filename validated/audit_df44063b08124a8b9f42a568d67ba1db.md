### Title
PedPoP encryption accepts an identity encryption key, producing a publicly-known ECDH shared point that exposes encrypted secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
In the PedPoP DKG, each participant registers a long-lived encryption public key (`enc_key`) inside its `EncryptionKeyMessage`. Senders encrypt secret shares to a recipient by performing ECDH between a fresh per-message scalar and the recipient's registered `enc_key`. `EncryptedMessage` carries a proof-of-possession for the per-message key, but **no proof of possession or validity check exists for the registered `enc_key`**: `Decryption::register` inserts `msg.enc_key` verbatim [1](#0-0) . A malicious participant can therefore register the identity point (`C::G::identity()`), which passes `C::read_G` deserialization and makes every ECDH share `identity * k = identity`, i.e. a constant, publicly computable cipher input [2](#0-1) .

### Finding Description
`Encryption::encrypt` calls `encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)`, where `enc_keys[participant]` is attacker-controlled [3](#0-2) . Inside `encrypt`, the shared point is `ecdh(private, public) = public * private` [2](#0-1) , and the ChaCha20 keystream is derived deterministically from `context || ecdh.to_bytes()` [4](#0-3) . If `public` is the identity, the shared point is the identity for every message and every sender in that session, so anyone who obtains the `EncryptedMessage` ciphertext can recompute `cipher(context, identity)` and recover the `SecretShare` plaintext. The recipient's own decryption still "works" (produces the same keystream), so no error is raised and `calculate_share` proceeds normally [5](#0-4) . The `pop` field binds only the per-message `key`, not `enc_key` [6](#0-5) . This mirrors CVE-2020-17508's memory-disclosure class: secret material is disclosed to parties who were never meant to read it, triggered purely by attacker-supplied bytes fed to `EncryptionKeyMessage::read` / `Commitments::read`.

### Impact Explanation
Every `SecretShare` encrypted to the malicious participant is recoverable in the clear by any holder of the ciphertext. The DKG shares are relayed through coordinator/tributary infrastructure, so ciphertext exposure extends beyond the malicious recipient. If the adversary additionally compromises or observes shares destined for other participants (or colludes across the threshold boundary), learned shares reduce the effective security of the generated group key; at minimum the confidentiality guarantee ("shares are only readable by their intended recipient") is silently voided, and a single observed share transcript reveals that participant's polynomial evaluation.

### Likelihood Explanation
Reachable by any unprivileged DKG participant: they simply serialize `EncryptionKeyMessage { enc_key: identity }` in round 1, which deserializes cleanly and is stored without any non-identity or PoP check [1](#0-0) . No other check in `verify_r1` inspects `enc_key` — it validates only the commitments PoK [7](#0-6) . Caveat: I could not fully confirm within this session that `C::read_G` for every in-scope ciphersuite accepts the identity encoding; Ristretto and k256 `GroupEncoding` decoders both accept it, and nothing in the PedPoP path re-checks for identity.

### Recommendation
In `Decryption::register` (or `EncryptionKeyMessage::read`), reject `msg.enc_key` if `bool::from(msg.enc_key.is_identity())`. Optionally require a Schnorr PoP over `enc_key` binding it to the participant and context, mirroring the per-message `pop` design, so keys with known discrete logs chosen to produce predictable ECDH outputs are impossible.

### Proof of Concept
```rust
// Attacker participant l registers an identity encryption key
let msg = EncryptionKeyMessage { msg: my_commitments, enc_key: C::G::identity() };
// ... serialize/broadcast msg; victim's machine calls encryption.register(l, msg)
// Victim encrypts share: ecdh = identity * k = identity
// Observer recomputes the keystream without any secret:
let key = Zeroizing::new(C::G::identity());
let mut cipher = cipher::<C>(context, &key); // identical ChaCha20 state
cipher.apply_keystream(ciphertext.as_mut()); // recovers SecretShare bytes
```
All decryption and share-verification checks pass on the victim side, so the attack is undetectable in-protocol.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-132)
```rust
fn cipher<C: Ciphersuite>(context: [u8; 32], ecdh: &Zeroizing<C::G>) -> ChaCha20 {
  // Ideally, we'd box this transcript with ZAlloc, yet that's only possible on nightly
  // TODO: https://github.com/serai-dex/serai/issues/151
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"encryption_key");

  let mut ecdh = ecdh.to_bytes();
  transcript.append_message(b"shared_key", ecdh.as_ref());
  ecdh.as_mut().zeroize();

  let zeroize = |buf: &mut [u8]| buf.zeroize();

  let mut key = Cc20Key::default();
  let mut challenge = transcript.challenge(b"key");
  key.copy_from_slice(&challenge[.. 32]);
  zeroize(challenge.as_mut());

  // Since the key is single-use, it doesn't matter what we use for the IV
  // The issue is key + IV reuse. If we never reuse the key, we can't have the opportunity to
  // reuse a nonce
  // Use a static IV in acknowledgement of this
  let mut iv = Cc20Iv::default();
  // The \0 is to satisfy the length requirement (12), not to be null terminated
  iv.copy_from_slice(b"DKG IV v0.2\0");

  // ChaCha20 has the same commentary as the transcript regarding ZAlloc
  // TODO: https://github.com/serai-dex/serai/issues/151
  let res = ChaCha20::new(&key, &iv);
  zeroize(key.as_mut());
  res
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-324)
```rust
fn pop_challenge<C: Ciphersuite>(
  context: [u8; 32],
  nonce: C::G,
  key: C::G,
  sender: Participant,
  msg: &[u8],
) -> C::F {
  let mut transcript = RecommendedTranscript::new(b"DKG Encryption Key Proof of Possession v0.2");
  transcript.append_message(b"context", context);

  transcript.domain_separate(b"proof_of_possession");

  transcript.append_message(b"nonce", nonce.to_bytes());
  transcript.append_message(b"key", key.to_bytes());
  // This is sufficient to prevent the attack this is meant to stop
  transcript.append_message(b"sender", sender.to_bytes());
  // This, as written above, doesn't hurt
  transcript.append_message(b"message", msg);
  // While this is a PoK and a PoP, it's called a PoP here since the important part is its owner
  // Elsewhere, where we use the term PoK, the important part is that it isn't some inverse, with
  // an unknown to anyone discrete log, breaking the system
  C::hash_to_F(b"DKG-encryption-proof_of_possession", &transcript.challenge(b"schnorr"))
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

**File:** crypto/dkg/pedpop/src/lib.rs (L311-334)
```rust
    let mut batch = BatchVerifier::<Participant, C::G>::new(commitment_msgs.len());
    let mut commitments = HashMap::new();
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

**File:** crypto/dkg/pedpop/src/lib.rs (L476-491)
```rust
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
```
