### Title
Unauthenticated DKG shares reveal the ECDH message key via blame proofs, enabling secret share recovery — PoP only batch-queued, never verified before blame emission - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
CVE-2021-29969's class is "attacker-injected data processed before the authentication/handshake bound it, instead of being ignored." The analog in Serai's PedPoP DKG is `KeyMachine::calculate_share`: `Encryption::decrypt` only *queues* the message's Schnorr proof-of-possession into a `BatchVerifier` and immediately returns an `EncryptionKeyProof` revealing the ECDH shared key. If the decrypted bytes are not a canonical scalar, `calculate_share` returns `InvalidShare { blame: Some(blame) }` *before* `batch.verify_with_vartime_blame()` ever runs — so the ECDH-revealing blame is emitted for a message whose PoP was never verified. This is exactly the attack the `EncryptedMessage` PoP was designed to prevent (crypto/dkg/pedpop/src/encryption.rs:83-90).

### Finding Description
`EncryptedMessage` carries a Schnorr PoP over `(context, key, sender, msg)` precisely so an attacker cannot trigger a blame proof revealing a recipient's ECDH shared key for a message key they did not create (encryption.rs:83-91, `pop_challenge` at encryption.rs:302-324).

In `Encryption::decrypt` (encryption.rs:469-500), `msg.pop.batch_verify` only *queues* the PoP verification; decryption proceeds and an `EncryptionKeyProof { key: ecdh(enc_priv, msg.key), dleq }` is returned unconditionally.

In `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs:474-499):
```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
```
When `from_repr` fails, the function returns early with `blame: Some(blame)` — the queued PoP verification at `batch.verify_with_vartime_blame()` (line 493) is never reached. `SecretShare::read`/`from_repr` is the only "authentication" applied to the plaintext, and it runs on data the attacker caused to be produced before the PoP — the actual handshake binding the message to its sender — completed.

### Impact Explanation
An unprivileged party who can feed an `EncryptedMessage` to a victim's `calculate_share` (e.g., by injecting shares in a DKG round) obtains, via the emitted blame proof, the point `enc_priv_victim * msg.key` for an arbitrary attacker-chosen `msg.key` — a static-ECDH oracle. Concretely: Eve observes Alice's `EncryptedMessage` to Bob (containing Alice's secret share, encrypted under `ecdh(k, enc_pub_Bob)` where `msg.key = k*G`). Eve submits a forged `EncryptedMessage` to Bob with `key = k*G` (Alice's message key) and garbage ciphertext/PoP. The garbage decrypts to non-canonical scalar bytes with high probability, so Bob emits `EncryptionKeyProof { key = enc_priv_Bob * k*G }` — exactly the ChaCha20 keystream seed (`cipher(context, ecdh)`) for Alice's real message. Eve XORs it against Alice's observed ciphertext and recovers Alice's secret share for Bob. Combined across participants this leaks DKG secret shares — key-share-recovery impact. The early return path defeats the sole mitigation (the PoP) documented in the code.

### Likelihood Explanation
The attacker only needs to deliver a crafted `EncryptedMessage` byte blob to an honest participant running `calculate_share` and observe the resulting blame (blame proofs are designed to be published/verified by third parties via `AdditionalBlameMachine`). No threshold collusion, no valid signature, and no knowledge of any secret is required: `msg.key` is a copied public point, and non-canonical decrypted bytes occur with high probability for arbitrary ciphertexts (a ~252-bit field in a 256-bit repr). The same early-blame path also fires on the share-verification failure path only after batch verification — but the `from_repr` path bypasses it entirely.

### Recommendation
Verify the PoP for each message *before* returning or using any `EncryptionKeyProof`. Options: perform `msg.pop.verify(...)` synchronously inside `Encryption::decrypt` (or verify the queued batch before the `from_repr` check / before constructing blame), and only attach `blame: Some(...)` for messages whose PoP actually verified — otherwise emit `blame: None` (the `BatchId::Decryption` path already models this distinction). Never release the ECDH shared point for a message that failed or skipped PoP verification.

### Proof of Concept
1. Run PedPoP round 1: honest Alice produces `EncryptedMessage` to Bob with `msg.key = K_A = k*G` encrypting `SecretShare` under `cipher(context, enc_priv_Bob * K_A)`. Eve observes the serialized message.
2. Eve constructs `EncryptedMessage { key: K_A, pop: <invalid SchnorrSignature>, msg: <32 garbage bytes> }` and submits it to Bob as her share.
3. Bob's `calculate_share` calls `decrypt`: PoP is queued (never verified), `ecdh(enc_priv_Bob, K_A)` is computed and wrapped into `EncryptionKeyProof`, garbage bytes are XOR-decrypted — almost certainly non-canonical.
4. `from_repr` fails → `PedPoPError::InvalidShare { participant: Eve, blame: Some(EncryptionKeyProof{ key: enc_priv_Bob * K_A, dleq }) }` is returned; `batch.verify_with_vartime_blame()` is never executed.
5. Eve reads the published `blame.key = enc_priv_Bob * K_A`, re-derives `cipher::<C>(context, &key)`, and XORs it with Alice's original ciphertext — recovering Alice's secret share to Bob. No valid PoP was ever required. [1](#0-0) [2](#0-1) [3](#0-2)

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
