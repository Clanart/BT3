Checking whether `read_G` rejects non-prime-order/identity points for the ciphersuites used, since a low-order `msg.key` in `EncryptedMessage` could leak the recipient's ECDH secret via the published blame proof.### Title
Blame proof reveals the ECDH key for an unauthenticated `msg.key`, leaking victim secret shares - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The bug class is "credentials are disclosed to an attacker-registered endpoint before the endpoint is authenticated." In PedPoP, `KeyMachine::calculate_share` hands out an `EncryptionKeyProof` — the recipient's ECDH shared key `b * msg.key` — when the decrypted share fails canonical deserialization, **before** the batch verifier has checked the message's proof-of-possession. A malicious participant can set `msg.key` to a value copied from an honest participant's message, trigger the early return, and cause the victim's per-message decryption key to be published in an otherwise-valid blame proof.

### Finding Description
In `Encryption::decrypt`, the PoP (`msg.pop`) is only *queued* into the `BatchVerifier`, while the ECDH key `ecdh::<C>(&self.enc_key, msg.key)` and its `EncryptionKeyProof` are computed and returned immediately. In `KeyMachine::calculate_share`, for each share the code decrypts, then calls `C::F::from_repr(share_bytes.0)` and early-returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }` on failure — before `batch.verify_with_vartime_blame()` at line 493 ever runs. The same applies to the share-verification loop ordering: the `blame` object containing `proof.key` is emitted while the PoP is still unverified. [1](#0-0) [2](#0-1) 

This is exactly the attack the `pop` field was added to prevent: the comment on `EncryptedMessage` states that without PoP verification, an attacker claiming another participant's message key `X` causes the recipient to reveal `bX`, leaking the honest message. [3](#0-2)  The PoP check exists, but it is only enforced inside `decrypt_with_proof` during blame adjudication, at which point `proof.key` has already been broadcast by the accuser in `ProcessorMessage::InvalidShare`/`VerifyBlame`. [4](#0-3) [5](#0-4) 

Note `blame_internal` correctly blames the sender when the PoP is invalid (the copied `pop` binds `sender`, so replaying Alice's signature under Eve's index fails verification). The damage is not a mis-attribution — it is that the blame *proof payload* containing `bX` is already public, so anyone can run `cipher(context, bX)` against Alice's real ciphertext to Bob and recover Bob's secret share.

### Impact Explanation
Disclosure of a victim's secret share. Anyone observing the published `EncryptionKeyProof` can derive the ChaCha20 key via `cipher` and decrypt the honest participant's `EncryptedMessage` to the targeted recipient, recovering the secret share in the clear. If the attacker collects enough leaked shares (e.g., by repeating across participants/sessions), this degrades toward threshold key-share recovery. At minimum it defeats the confidentiality guarantee that per-message keys were designed to preserve. [6](#0-5) 

### Likelihood Explanation
Reachable by any unprivileged DKG participant: the attacker sends a crafted `EncryptedMessage` whose `key` is copied from a victim's broadcast message and whose `msg` bytes decrypt to a non-canonical scalar (trivially satisfiable — any garbage decrypts to invalid repr with overwhelming probability; the PoP only needs to fail-or-pass, since it is never checked on this path). The victim's `calculate_share` returns `InvalidShare` with the blame proof, which the processor publishes via `VerifyBlame`. No collusion, timing, or privileged access is required; the cost is the attacker being (correctly) blamed after the fact, which does not undo the leak.

### Recommendation
Verify the PoP before returning the blame payload. Options: in `calculate_share`, run a dedicated `batch` containing only the PoP statements before any `from_repr` early return that emits `blame`, or compute the `EncryptionKeyProof` lazily — only after `batch.verify_with_vartime_blame()` confirms the PoP — withholding `Some(blame)` whenever `BatchId::Decryption(l)` failed. Alternatively, verify `msg.pop` synchronously inside `Encryption::decrypt` prior to computing `ecdh`/`proof`, matching the order already used in `decrypt_with_proof`. [7](#0-6) 

### Proof of Concept
1. Eve participates in a PedPoP session with honest participants Alice and Bob. She observes Alice's `EncryptedMessage` to Bob, whose `key` field is `X` (Alice's per-message key pub).
2. In her own share slot to Bob, Eve submits `EncryptedMessage { key: X, pop: <any signature she can produce for a key she owns won't fit; she instead needs pop to merely be queued> , msg: <arbitrary bytes> }`. Concretely: Eve generates her own `K_e = k_e·G` but reuses nothing — simplest variant: Eve sets `msg.key = X` and signs `pop` under a key `X` she cannot sign for. The PoP verification is deferred to the batch, so this does not matter on the leaking path.
   - To satisfy the code path, Eve only needs `EncryptedMessage::read` to succeed and the decrypted bytes to be a non-canonical scalar (true for ~1 − 2⁻²⁵² of random ciphertexts under Ristretto).
3. Bob's `calculate_share` calls `self.encryption.decrypt`, which computes `proof.key = b·X` (Bob's ECDH key for Alice's real message) and a valid DLEq proving correctness against Bob's `enc_pub_key`.
4. `from_repr` on the garbage plaintext fails → returns `PedPoPError::InvalidShare { participant: Eve, blame: Some(proof) }`; the processor emits `ProcessorMessage::InvalidShare` carrying `proof.serialize()`.
5. Anyone reading `proof.key = b·X` computes `cipher::<C>(context, b·X)` and XORs the keystream against Alice's original ciphertext → recovers Bob's secret share from Alice in plaintext. Bob (and the coordinator, via `AdditionalBlameMachine`) will blame Eve, but the share is already disclosed. [8](#0-7)

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-392)
```rust
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
