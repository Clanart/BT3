### Title
Blame-proof ECDH key emitted before the queued proof-of-possession is verified, enabling key co-option to leak a victim's secret share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
PedPoP defends against a "key co-option" attack — where Eve reuses the per-message public key `X` from Alice's encrypted share to Bob so that Bob's blame proof reveals `b·X`, thereby also decrypting Alice's message — by attaching a Schnorr proof-of-possession (PoP) to each `EncryptedMessage`. However, `Encryption::decrypt` only *queues* the PoP into the shared `BatchVerifier` and immediately computes the ECDH shared key and the revealable `EncryptionKeyProof`. In `KeyMachine::calculate_share`, if the decrypted bytes fail `C::F::from_repr`, the function returns `PedPoPError::InvalidShare { blame: Some(blame) }` via `?` **before** `batch.verify_with_vartime_blame()` ever runs. The PoP is therefore never checked on this path, and the victim emits a publishable blame proof containing the ECDH shared key for an attacker-forged message — exactly the "massive side effect" the PoP was added to prevent. This maps the incident's information-leakage class: an unprivileged participant causes disclosure of secret material (a secret share) belonging to an honest party.

### Finding Description
`EncryptedMessage` documents the attack: if Eve copies Alice's per-message key `X` into her own message to Bob, Bob's blame argument reveals `b·X`, which also decrypts Alice's message to Bob; the PoP exists to prevent it [1](#0-0) .

`Encryption::decrypt` verifies the PoP via `msg.pop.batch_verify(rng, batch, batch_id, ...)` — a deferred, queued check — then immediately computes `ecdh(&self.enc_key, msg.key)`, decrypts, and returns `(msg, EncryptionKeyProof { key, dleq })` [2](#0-1) .

`KeyMachine::calculate_share` consumes that tuple and, on non-canonical decrypted share bytes, returns early with `InvalidShare { participant: l, blame: Some(blame) }`; `batch.verify_with_vartime_blame()` at the end of the loop is unreachable on this path, so the queued PoP verification is skipped [3](#0-2) .

Wire-side feasibility: `EncryptedMessage::read` accepts an attacker-supplied `key` (any canonical point, including a copied `X`) and a `pop` parsed via `SchnorrSignature::read`, which imposes only canonical encoding checks — an invalid PoP is still accepted structurally [4](#0-3) [5](#0-4) .

The leaked `EncryptionKeyProof.key` equals `enc_key_bob · X`, verified public-side against the registered keys in `Decryption::decrypt_with_proof` via the DLEq, and directly usable to decrypt the blamed ciphertext with `cipher(context, &proof.key)` [6](#0-5) .

### Impact Explanation
Bob's emitted blame proof reveals `b·X`, the identical ECDH shared key Alice used for her encrypted share to Bob (`cipher` is keyed solely by `context` and the ECDH point, with a fixed IV) [7](#0-6) . Publishing the blame — the designed response to `InvalidShare` — lets every observer decrypt Alice's `EncryptedMessage` and recover Alice's Pedersen secret share `f_alice(bob)`. That is protocol-internal secret data (a share contributing to Bob's threshold secret and revealing Alice's polynomial evaluation) leaked by an unprivileged party's forged message — a genuine information-disclosure analog with `Medium`/`High` severity per the code's own "massive side effect" assessment.

### Likelihood Explanation
Requires Eve to observe Alice's `EncryptedMessage` to Bob (or at least its `key` field `X`), and Bob to publish the blame proof returned in `PedPoPError::InvalidShare`. Both steps use only public/wire-level data and the protocol's own blame flow; in Serai's deployment, blame material is relayed for `VerifyBlame` adjudication, which is precisely when the revealing key is disseminated [8](#0-7) . Note: `AdditionalBlameMachine`/`decrypt_with_proof` would correctly blame Eve's invalid PoP, but only after the leaking proof was already produced and distributed — the leak is not prevented, merely attributed.

### Recommendation
In `Encryption::decrypt`/`calculate_share`, do not emit `EncryptionKeyProof` for a message whose PoP has not yet been verified. Concretely: run the `BatchId::Decryption` (PoP) batch verification for each message *before* deserialization/returning its blame (e.g., verify the per-sender PoP immediately or restructure `calculate_share` so the `from_repr` early-return path first completes `batch.verify_with_vartime_blame()` and maps PoP failures to `blame: None`). Alternatively, produce the `EncryptionKeyProof` lazily only after the PoP batch succeeds.

### Proof of Concept
1. Alice's `generate_secret_shares` emits `EncryptedMessage { key: X, pop: σ_A, msg: Enc_bX(share) }` to Bob, where `X = x·G`.
2. Eve observes `X` and builds her own message to Bob: `key = X`, any structurally-valid `pop` (e.g., `(R, s)` canonical bytes — it will fail verification, but that check is deferred), and arbitrary ciphertext bytes.
3. Bob's `KeyMachine::calculate_share` calls `Encryption::decrypt`: the PoP is queued into `batch` under `BatchId::Decryption(Eve)`, `ecdh(bob_enc, X) = b·X` is computed, the garbage ciphertext is "decrypted," and `(msg, EncryptionKeyProof{key: b·X, dleq})` is returned.
4. `C::F::from_repr(share_bytes.0)` fails → `calculate_share` returns `InvalidShare { participant: Eve, blame: Some(proof) }` immediately; `batch.verify_with_vartime_blame()` is never reached, so Eve's invalid PoP is never detected locally.
5. Bob's blame submission (handled via `CoordinatorMessage::VerifyBlame`) carries `proof.key = b·X`. Anyone holding Alice's original ciphertext can now run `cipher(context, b·X).apply_keystream(...)` and recover Alice's secret share to Bob — the precise leak the PoP was introduced to prevent.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-393)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-501)
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
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L476-499)
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
    }
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```

**File:** crypto/schnorr/src/lib.rs (L50-53)
```rust
  /// Read a SchnorrSignature from something implementing Read.
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    Ok(SchnorrSignature { R: C::read_G(reader)?, s: C::read_F(reader)? })
  }
```

**File:** processor/src/key_gen.rs (L504-549)
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
```
