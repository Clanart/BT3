### Title
Registered encryption key lacks proof-of-possession/identity check, leaking a participant's secret shares to any observer - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
Analogous to CVE-2022-3540 (improper handling of attacker-supplied input lets a party extract other users' private data), PedPoP accepts each participant's `enc_key` from `EncryptionKeyMessage` with no proof-of-possession and no exclusion of keys with known discrete logarithms. A participant who registers `enc_key = C::generator()` (or any key with known dlog) causes every secret share addressed to them to be encrypted under a publicly computable ECDH key, so any observer of the relayed ciphertexts can decrypt and recover that participant's full FROST secret share.

### Finding Description
`EncryptionKeyMessage::read` reads `enc_key` as raw attacker-controlled bytes via `C::read_G` with no validity or ownership check [1](#0-0) . In `SecretShareMachine::verify_r1`, each commitment message is passed to `self.encryption.register(l, msg)`, and the only verification performed is the Schnorr PoK over `commitments[0]` — nothing binds or validates `enc_key` [2](#0-1) . `Decryption::register` simply inserts the supplied key [3](#0-2) .

Senders then encrypt to that key: `Encryption::encrypt` calls `encrypt(..., self.decryption.enc_keys[&participant], msg)` [4](#0-3) , and `encrypt` derives the shared key as `ecdh(&key, to)` = `key * enc_key` where `key` is the per-message ephemeral scalar [5](#0-4) . If `enc_key` has known discrete log `k` (e.g., `k = 1` for the generator), the shared point is `msg.key * k`, computable by anyone from the public ciphertext field `msg.key`. Anyone can then run `cipher(context, ecdh)` and `apply_keystream` to recover the plaintext `SecretShare` [6](#0-5) .

The per-message PoP only prevents co-opting someone else's *per-message* key — it does not constrain the *registered* key [7](#0-6) .

### Impact Explanation
Every dealer encrypts participant `e`'s share `f_j(e)` to `e`'s registered key. With a known-dlog `enc_key`, an unprivileged observer (e.g., anyone seeing coordinator-relayed share messages, not just the intended recipient) decrypts all `t`-dealer shares for `e`, sums them via Lagrange/interpolation to obtain `e`'s FROST secret share, and can thereafter produce valid signature shares indistinguishable from `e`'s — defeating participant accountability and contributing toward threshold compromise. This is key-share recovery reachable purely from publicly transmitted bytes.

### Likelihood Explanation
Exploitation requires only that a participant publish a malicious `enc_key` in their commitment message — a public input fully under their control. No PoP, subgroup/exponential check, or known-dlog exclusion exists, so the attack always succeeds. Confidentiality of the recipient channel is not assumed by the protocol (only authentication is required per the docs), and blame workflows intentionally publish decryption keys, confirming ciphertexts are treated as non-confidential.

### Recommendation
Require a proof-of-possession for the registered `enc_key` in the commitments round (e.g., a Schnorr signature over the context and participant index under `enc_key`), and reject the identity element. This binds the encryption key to a party knowing its discrete log, closing the known-dlog/co-option leak.

### Proof of Concept
1. Participant `e` calls `generate_coefficients`, then replaces `enc_key` in its `EncryptionKeyMessage` with `C::generator()` before broadcasting (the field is attacker-controlled serialized bytes read by `EncryptionKeyMessage::read`).
2. All dealers pass `verify_r1` (PoK over commitments only) and send `EncryptedMessage`s to `e` encrypted as `cipher(context, msg.key * 1)`.
3. Observer reads each `EncryptedMessage` (`key: C::G` field is public), computes `cipher(context, msg.key)`, applies the keystream to `msg.msg`, parses the `SecretShare` scalar `f_j(e)` via `C::F::from_repr`.
4. Observer sums `Σ f_j(e)` to recover `e`'s secret share, then forges `e`'s signature shares in subsequent FROST sessions.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L81-92)
```rust
pub struct EncryptedMessage<C: Ciphersuite, E: Encryptable> {
  key: C::G,
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
  msg: Zeroizing<E>,
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L153-156)
```rust
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
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
