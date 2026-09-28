I found a concrete analog. Confirming the exact code path once more — the early `from_repr` error returns a blame proof before the batch PoP verification runs.### Title
`calculate_share` emits a decryption-key blame proof before verifying the message's proof-of-possession, letting an attacker reuse a victim's encryption key to force disclosure of that victim's secret share - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The reported bug class is an ordering flaw: a distinguishable response (`ACCOUNT_ALREADY_LINKED`) is produced before an authentication check, leaking private information to an unauthenticated attacker. The exact same shape exists in `KeyMachine::calculate_share` in `crypto/dkg/pedpop/src/lib.rs:463-499`. `Encryption::decrypt` (`crypto/dkg/pedpop/src/encryption.rs:469-501`) unconditionally computes an `EncryptionKeyProof` containing the ECDH shared point `b * msg.key` plus a DLEq proof, and merely *queues* the per-message proof-of-possession (`pop`) Schnorr signature into a `BatchVerifier`. Back in `calculate_share`, the canonical-scalar check on the decrypted plaintext (`C::F::from_repr`, line 480) returns `Err(PedPoPError::InvalidShare { participant: l, blame: Some(blame) })` early — before `batch.verify_with_vartime_blame()` at line 493 ever verifies the PoP. The blame proof, which reveals the recipient's ECDH key for that message key, is therefore emitted for a message that was never authenticated.

### Finding Description
The `pop` field exists specifically to prevent key-reuse attacks. The comment at `crypto/dkg/pedpop/src/encryption.rs:83-90` spells it out: without the PoP, Eve can observe Alice encrypt to Bob with per-message key `X`, send Bob her own message "also claiming to use X", and when Bob publishes a blame argument he reveals `bX` (Bob's private enc key `b` times `X`), which decrypts Alice's message to Bob — "a massive side effect".

The PoP closes this only if the blame proof is released *after* the PoP verifies. But `decrypt` returns `(msg.msg, EncryptionKeyProof { key: ecdh(enc_key, msg.key), dleq })` immediately, with the PoP only enqueued under `BatchId::Decryption(l)`. `calculate_share` then does:

```rust
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
```

(lib.rs:479-482). If the decrypted bytes are not a canonical scalar, the function exits with `blame: Some(blame)` — the DLEq-attested ECDH key — without ever running `batch.verify_with_vartime_blame()` (line 493), which is the only place the PoP signature is checked. By contrast, when the batch *does* run and the PoP fails, the error path correctly uses `blame: None` (lib.rs:494-495). So the early-return ordering reintroduces precisely the attack the PoP was designed to prevent: the blame credential is disclosed before authentication.

### Impact Explanation
When `calculate_share` errors with `InvalidShare { blame: Some(...) }`, the protocol hands the accuser a publicly-verifiable blame proof (`EncryptionKeyProof` containing `key = b*X` and a DLEq proof, verified publicly by `Decryption::decrypt_with_proof` at encryption.rs:381-393). Per the DKG spec (`spec/cryptography/Distributed Key Generation.md:19-26`), this key is *published* so all participants can decrypt and judge blame. The attacker thus obtains `b*X` — the exact ChaCha20 cipher key (`cipher()` at encryption.rs:101-133 derives key+IV solely from context and the ECDH point with a static IV) — for the victim Alice→Bob share message whose key `X` was reused. Alice's encrypted `SecretShare` to Bob becomes publicly decryptable, leaking a DKG secret share despite the PoP defense. This is secret-key-material disclosure reachable with public inputs (a crafted `EncryptedMessage` fed to `calculate_share`), matching the report's "sensitive data exposure via pre-auth error path" class.

### Likelihood Explanation
The attacker is a DKG participant (or an observer who sees the shares map, which is transmitted as a whole in `CoordinatorMessage::Shares` — `processor/src/key_gen.rs:370-413`). They take any honest participant's `EncryptedMessage`, copy its `key` field `X`, attach an arbitrary/invalid `pop` signature, and supply a random ciphertext. `cipher()` applies the keystream unconditionally, so the "decrypted" bytes are pseudorandom 32-byte strings; for Ristretto/ed25519 scalars the group order (~2^252) means a random 32-byte string is non-canonical with probability ≈ 15/16, so the early `from_repr` failure — and hence `blame: Some(b*X)` — triggers on the first attempt with overwhelming probability. Even if it lands canonical, the attack retries freely; either way the sender is blamed, so repeated attempts are indistinguishable from ordinary faults. No collusion, no broken BFT, no leaked key required — just unauthenticated bytes into `EncryptedMessage::read`/`calculate_share`.

### Recommendation
Authenticate before releasing the blame credential. Concretely, in `KeyMachine::calculate_share` the PoP verification must complete (or be guaranteed) before any `EncryptionKeyProof` leaves the function: either verify `msg.pop` eagerly inside `Encryption::decrypt` before returning the proof, or buffer the would-be error and only attach `Some(blame)` after `batch.verify_with_vartime_blame()` confirms the decryption/PoP statement for that participant. Messages that fail PoP verification must always produce `blame: None`, matching the existing `BatchId::Decryption` path. Alternatively, gate the `from_repr` early-return behind a flag that is only converted into `InvalidShare` once the batch has cleared.

### Proof of Concept
Setup: 3-of-3 PedPoP over Ristretto; participants A(lice)=1, E(ve)=2, B(ob)=3. During round 2 each sender broadcasts `EncryptedMessage`s; Eve records Alice→Bob message `m_A = { key: X_A, pop: σ_A, msg: c_A }`.

1. Eve constructs `m_E = EncryptedMessage { key: X_A, pop: <arbitrary bytes parsed as SchnorrSignature>, msg: <random 32-byte ciphertext> }`, serializes it, and sends it to Bob as her share message.
2. Bob's `KeyMachine::calculate_share` calls `encryption.decrypt(...)`: it queues `m_E.pop` into the batch under `BatchId::Decryption(2)` but returns immediately with `share_bytes = keystream(b·X_A) ⊕ c_E` and `blame = EncryptionKeyProof { key: b·X_A, dleq }`.
3. With probability ≈15/16 `C::F::from_repr(share_bytes)` fails; `calculate_share` returns `Err(InvalidShare { participant: 2, blame: Some(blame) })` — never reaching `batch.verify_with_vartime_blame()`, so the invalid PoP is irrelevant.
4. Bob publishes `blame` per the blame protocol. Anyone verifies the DLEq (`decrypt_with_proof`, encryption.rs:381-393) and obtains `b·X_A`.
5. `cipher(context, b·X_A)` reconstructs the ChaCha20 keystream (static IV `"DKG IV v0.2\0"`, encryption.rs:124-126); XOR against `c_A` recovers Alice's secret share to Bob — disclosure of DKG secret material caused solely by an error credential emitted before authentication, i.e., the `ACCOUNT_ALREADY_LINKED`-before-`SESSION_MISSING` ordering flaw applied to PedPoP blame. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-97)
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
  msg: Zeroizing<E>,
}

fn ecdh<C: Ciphersuite>(private: &Zeroizing<C::F>, public: C::G) -> Zeroizing<C::G> {
  Zeroizing::new(public * private.deref())
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L101-127)
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

```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-396)
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
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L478-500)
```rust
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
