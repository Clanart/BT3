### Title
Unvalidated per-participant encryption key enables public decryption of DKG secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to GHSA-g7p8-r2ch-4rmf (a malicious node causing disclosure of sensitive shared state), a participant in Serai's PedPoP DKG can register an encryption public key whose discrete logarithm is public (e.g., the group identity, or `k*G` for a publicly chosen `k`). `Decryption::register` stores `msg.enc_key` with no identity check and no proof-of-possession, so every `EncryptedMessage` addressed to that participant is encrypted under an ECDH secret that any unprivileged observer can compute. The secret-share ciphertexts broadcast over the authenticated channel are therefore decryptable by anyone, disclosing a real FROST secret share — not just to the malicious registrant, but to all passive observers of the transcript.

### Finding Description
In `EncryptionKeyMessage::read`, `enc_key` is loaded via `C::read_G` and in `Decryption::register` it is inserted into `enc_keys` unconditionally — there is no `is_identity` rejection, no proof-of-possession, and no binding that the registrant knows the discrete log [1](#0-0) [2](#0-1) . Senders encrypt each `SecretShare` as `cipher(context, ecdh(key, to)).apply_keystream(msg)` where `to` is the recipient's registered `enc_key` and `key` is a fresh per-message scalar whose public part is attached to the ciphertext [3](#0-2) . `ecdh` is a raw multiplication `public * private` with no validation [4](#0-3) .

If the registered `enc_key` is the identity point, `ecdh(key, identity) = identity` for every message, so the ChaCha20 keystream — derived deterministically from `context` and `ecdh.to_bytes()` with a static IV — is computable by anyone who knows `context` (public session data) [5](#0-4) . Equivalently, registering `enc_key = k*G` for a publicly chosen `k` lets any holder of `k` compute `k * msg.key` and decrypt.

Note the PoP (`pop`) protects the per-message key, not the registered encryption key; the only documented mitigation targets a different attack (key co-option causing misdirected blame) [6](#0-5) . These shares are generated in `SecretShareMachine::generate_secret_shares` and encrypted via `self.encryption.encrypt(rng, l, ...)` using the attacker's registered key [7](#0-6) .

### Impact Explanation
Any unprivileged observer of the DKG transcript (the share ciphertexts are distributed to all participants over the authenticated broadcast channel, not just the recipient) can recover the plaintext `SecretShare` addressed to the malicious registrant. A secret share is normally confidential to its recipient; here it becomes public. This reduces the effective secrecy budget of the threshold key: an adversary who has compromised `t-1` other participants, or who later compromises `t-1` shares through a second vector, obtains the `t`-th share passively and can reconstruct the group private key / forge signatures. It also means the malicious participant can cause their share to leak while maintaining plausible deniability (no on-protocol evidence they disclosed it), undermining the blame model that assumes key reuse is "effectively Eve revealing themselves."

### Likelihood Explanation
Requires a malicious DKG participant who registers a weak encryption key — an unprivileged action reachable purely through the public `EncryptionKeyMessage` bytes fed to `EncryptionKeyMessage::read`/`register`. No honest party is required to misbehave; senders encrypt to whatever `enc_key` was registered. Medium likelihood/medium impact consistent with the source advisory's Medium rating: confidentiality of a threshold share is degraded, but full key compromise still needs `t-1` additional shares.

### Recommendation
Validate `enc_key` on registration: reject identity and, on curves with a cofactor (e.g., Ed25519 via dalek-ff-group), reject non-prime-order/torsion points (`(enc_key * cofactor).is_identity()` check or multiply-by-`r` check). Additionally, require a proof-of-possession (Schnorr signature over the session `context` and participant index) for `enc_key` in round 1, so a registrant must prove knowledge of the discrete log — making registered keys non-weak and non-attributable to a third party's dlog.

### Proof of Concept
1. Malicious participant `l` constructs round-1 `EncryptionKeyMessage` with `enc_key = C::G::identity()` (encodable by `C::read_G`) alongside valid `Commitments` and a valid PoK for coefficient zero.
2. All honest participants accept it — `verify_r1` only checks `msg.sig` (PoK for the polynomial coefficient), commitment count, and batch Schnorr verification; `enc_key` is registered unchecked [8](#0-7) .
3. Each honest sender computes `ecdh(k_i, identity) = identity` and derives the ChaCha20 keystream from `transcript(context || identity_bytes || "DKG IV v0.2\0")` — all public values — then broadcasts `EncryptedMessage { key: k_i*G, pop, msg: share XOR keystream }` to everyone.
4. Any observer replays the same transcript derivation and XORs the keystream against `msg` to recover `l`'s secret share in the clear.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-91)
```rust
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L95-97)
```rust
fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-133)
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
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L135-168)
```rust
fn encrypt<R: RngCore + CryptoRng, C: Ciphersuite, E: Encryptable>(
  rng: &mut R,
  context: [u8; 32],
  from: Participant,
  to: C::G,
  mut msg: Zeroizing<E>,
) -> EncryptedMessage<C, E> {
  /*
  The following code could be used to replace the requirement on an RNG here.
  It's just currently not an issue to require taking in an RNG here.
  let last = self.last_enc_key.to_bytes();
  self.last_enc_key = C::hash_to_F(b"encryption_base", last.as_ref());
  let key = C::hash_to_F(b"encryption_key", last.as_ref());
  last.as_mut().zeroize();
  */

  // Generate a new key for this message, satisfying cipher's requirement of distinct keys per
  // message, and enabling revealing this message without revealing any others
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
  let nonce = Zeroizing::new(C::random_nonzero_F(rng));
  let pub_nonce = C::generator() * nonce.deref();
  EncryptedMessage {
    key: pub_key,
    pop: SchnorrSignature::sign(
      &key,
      nonce,
      pop_challenge::<C>(context, pub_nonce, pub_key, from, msg.deref().as_ref()),
    ),
    msg,
  }
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

**File:** crypto/dkg/pedpop/src/lib.rs (L357-370)
```rust
    // Step 1: Generate secret shares for all other parties
    let mut res = HashMap::new();
    for l in self.params.all_participant_indexes() {
      // Don't insert our own shares to the byte buffer which is meant to be sent around
      // An app developer could accidentally send it. Best to keep this black boxed
      if l == self.params.i() {
        continue;
      }

      let mut share = polynomial(&self.coefficients, l);
      let share_bytes = Zeroizing::new(SecretShare::<C::F>(share.to_repr()));
      share.zeroize();
      res.insert(l, self.encryption.encrypt(rng, l, share_bytes));
    }
```
