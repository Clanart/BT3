### Title
PedPoP blame proof leaks the ECDH share-decryption key before the proof-of-possession batch is verified, letting an attacker recover shares addressed to an honest participant - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The Ironic bug class — an unprivileged requester causes credentials to be forwarded to an attacker-chosen destination — maps onto PedPoP's blame mechanism. `Encryption::decrypt` returns an `EncryptionKeyProof` that publicly reveals the ECDH shared key `b * X` (recipient's encryption secret times the sender's ephemeral message key) intended for blame adjudication. The code comments in `encryption.rs` explicitly acknowledge that revealing `bX` "reveal[s] Alice's message to Bob", and the per-message Schnorr PoP was added solely to prevent an attacker from reusing victim ephemeral keys to trigger that leak. However, `KeyMachine::calculate_share` can return that blame proof via an early `?` on a share-deserialization failure *before* the batched PoP verification runs, so the PoP never actually gates the leak. An attacker who replays the ephemeral key `X` from a victim's encrypted share message obtains a publicly-verifiable revelation of `b * X`, which is exactly the ChaCha20 keystream key for the victim's real share.

### Finding Description
`Encryption::decrypt` queues the PoP signature into a `BatchVerifier` (deferred verification), then unconditionally computes `key = ecdh(enc_key, msg.key)` and returns `(msg, EncryptionKeyProof { key, dleq })` [1](#0-0) . In `calculate_share`, the returned bytes are deserialized with `C::F::from_repr`, and on failure the function early-returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame) }` [2](#0-1) . The `batch.verify_with_vartime_blame()` call that would have checked the PoP only runs after the loop [3](#0-2) , so a malformed share byte string aborts the protocol while still emitting a blame proof — with the PoP never verified.

The comments acknowledge this exact attack: Eve reusing Alice's key `X` lets a blame proof reveal `bX`, "revealing Alice's message to Bob", and the PoP "is sufficient to prevent the attack this is meant to stop" [4](#0-3) . Because the PoP check is batched and skipped on the early return, the mitigation is bypassed: any `EncryptedMessage` whose ciphertext payload fails `from_repr` (trivially constructed, since `SecretShare::read` accepts arbitrary bytes [5](#0-4) ) yields a blame proof regardless of PoP validity.

`msg.key` is read with `C::read_G` (ciphersuite-level, no identity rejection — contrast with `Curve::read_G` which explicitly rejects identity [6](#0-5) ) via `EncryptedMessage::read` [7](#0-6) , so `key` is fully attacker-controlled. The cipher keystream is derived deterministically from `context || ecdh_bytes` with a static IV [8](#0-7) , so possession of `b * X` fully decrypts any message Alice sent to Bob using ephemeral key `X`.

### Impact Explanation
Secret share recovery / credential forwarding. In Serai's DKG, each participant sends every other participant an `EncryptedMessage<SecretShare>` containing one secret share, and blame proofs (`EncryptionKeyProof`, containing the raw ECDH point `b * X` plus a DLEq proving correctness against Bob's registered encryption key) are surfaced in `PedPoPError::InvalidShare` for publication/adjudication [9](#0-8) . An unprivileged DKG participant Eve who observes Alice→Bob ciphertext (sent over authenticated but non-confidential channels, or relayed through the coordinator) sends Bob a forged share message with `key = X` (Alice's ephemeral key) and garbage share bytes. Bob's `calculate_share` returns a blame proof revealing `b * X` before ever checking the PoP. Eve then derives the ChaCha20 keystream from `b * X` and decrypts Alice's real share to Bob. Repeating per sender, Eve recovers Bob's complete secret share; combined across enough victims this can collapse the threshold security of the resulting `ThresholdKeys` — an exact analog of Ironic forwarding a broadly-scoped credential to an attacker-influenced endpoint.

### Likelihood Explanation
Reachable by any DKG participant able to submit a share message to an honest participant (a normal protocol step: untrusted bytes reach `EncryptedMessage::read` → `calculate_share`). Requirements: (1) observe one victim ciphertext's `key` field — plaintext metadata, not encrypted; (2) craft a message with that `key`, any syntactically valid `pop` (it is never verified on this path), and share bytes that fail `from_repr` (e.g., an all-`0xFF` field repr, which `SecretShare::read` accepts unconditionally); (3) induce the honest party to emit the blame proof, which is the designed behavior of `InvalidShare` errors. No collusion, no privileged access, no repeated sessions required. The attack is deterministic — no race or probabilistic element.

### Recommendation
Verify the PoP synchronously before releasing the decryption key material in `Encryption::decrypt` — e.g., run `msg.pop.verify(...)` inline (or partition the batch so PoP failures short-circuit per-message) and only compute `ecdh`/`EncryptionKeyProof` after the PoP is known-valid, matching `decrypt_with_proof` which correctly checks `pop.verify` before using `proof.key` [10](#0-9) . Alternatively, in `calculate_share`, execute `batch.verify_with_vartime_blame()` (or at least the `BatchId::Decryption` statements) before returning any `blame` in `InvalidShare`, so no blame proof is emitted for a message whose PoP was never validated. Additionally consider rejecting identity `msg.key`/`enc_key` via the stricter `Curve::read_G` semantics.

### Proof of Concept
Setup: PedPoP session, `t`-of-`n`, context `ctx`. Bob (honest) registers `enc_pub_key = B = b·G`. Alice sends Bob `EncMsg_A→B = { key: X, pop: σ_A, msg: share_A→B XOR ks }` where `ks = ChaCha20(key=H(ctx, b·X), iv="DKG IV v0.2\0")`.

Attack by Eve (participant, has a KeyMachine-facing channel to Bob — i.e., submits share messages that Bob feeds to `calculate_share` after `EncryptedMessage::read`):

1. Eve observes `EncMsg_A→B` and extracts `X` (the `key` field is sent in the clear, written first by `write` [11](#0-10) ).
2. Eve constructs `EncMsg_E→B = { key: X, pop: any 64-byte SchnorrSignature-shaped blob, msg: 32×0xFF }`. `EncryptedMessage::read` accepts it: `C::read_G` parses `X`, `SchnorrSignature::read` and `SecretShare::read` accept the bytes [7](#0-6) [5](#0-4) .
3. Bob calls `calculate_share`. `decrypt` queues the (invalid) PoP into the batch — never executed — and returns `EncryptionKeyProof { key: b·X, dleq: DLEq(B, b·X) }` [12](#0-11) .
4. `C::F::from_repr(0xFF…)` fails → early return `Err(InvalidShare { participant: Eve, blame: Some(proof) })` at lib.rs:480-482; `batch.verify_with_vartime_blame()` at line 493 is never reached, so the invalid PoP is undetected.
5. The blame proof is surfaced for adjudication (per `InvalidShare` semantics and `blame_internal`/`decrypt_with_proof`, which accept this proof to decrypt the message [13](#0-12) ). Eve — and any observer of the published blame — now holds `b·X`.
6. Eve computes `ks = ChaCha20(H(ctx, (b·X).to_bytes()), "DKG IV v0.2\0")` per `cipher` [8](#0-7)  and XORs it against `EncMsg_A→B.msg`, recovering Alice's secret share for Bob.
7. Repeating steps 2-6 once per sender (each forged message early-returns independently), or across DKG sessions sharing Bob's `B`, lets Eve accumulate shares addressed to Bob — forwarding Bob's share-decryption credential to an attacker, the Serai analog of CVE-2026-42997.

Key code path:

```
Eve's bytes → EncryptedMessage::read → KeyMachine::calculate_share
  → Encryption::decrypt: pop.batch_verify (queued, never run)
                       → key = ecdh(enc_key_b, X)        // leaked
                       → return EncryptionKeyProof{key, dleq}
  → C::F::from_repr fails → early Err(InvalidShare{ blame: Some(proof) })
                            // batch.verify never executes
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L179-183)
```rust
  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.key.to_bytes().as_ref())?;
    self.pop.write(writer)?;
    self.msg.write(writer)
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L373-393)
```rust
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-499)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L46-47)
```rust
  #[error("invalid share (participant {participant}, blame {blame})")]
  InvalidShare { participant: Participant, blame: Option<EncryptionKeyProof<C>> },
```

**File:** crypto/dkg/pedpop/src/lib.rs (L264-268)
```rust
  fn read<R: Read>(reader: &mut R, _: ThresholdParams) -> io::Result<Self> {
    let mut repr = F::Repr::default();
    reader.read_exact(repr.as_mut())?;
    Ok(SecretShare(repr))
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L476-484)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L493-499)
```rust
    batch.verify_with_vartime_blame().map_err(|id| {
      let (l, blame) = match id {
        BatchId::Decryption(l) => (l, None),
        BatchId::Share(l) => (l, Some(blames.remove(&l).unwrap())),
      };
      PedPoPError::InvalidShare { participant: l, blame }
    })?;
```

**File:** crypto/frost/src/curve/mod.rs (L124-131)
```rust
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
