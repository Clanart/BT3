### Title
Unauthenticated ECDH oracle in `KeyMachine::calculate_share` leaks the per-message shared key before the proof-of-possession is verified - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`Encryption::decrypt` computes the ECDH shared key `enc_key * msg.key`, decrypts the ciphertext, and returns an `EncryptionKeyProof` revealing that shared key — while the Schnorr proof-of-possession binding `msg.key` to a known discrete log is only *queued* into a `BatchVerifier`, not checked. In `KeyMachine::calculate_share`, a malformed ciphertext body causes an early `?` return with `blame: Some(proof)` *before* `batch.verify_with_vartime_blame()` ever runs, so the PoP check is skipped entirely. A malicious DKG participant can therefore submit an `EncryptedMessage` whose `key` is the ephemeral public key copied from a victim's `EncryptedMessage` (whose discrete log they do not know) and receive, inside the `PedPoPError::InvalidShare` blame proof, the ECDH shared key needed to decrypt that victim's secret share. This mirrors CVE-2021-45450: an untrusted input steers the cipher's output/decryption primitive into an oracle usable to recover protected plaintext, bypassing the policy (PoP) meant to prevent it.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `Encryption::decrypt` does the following in order:

1. `msg.pop.batch_verify(...)` — only *queues* the Schnorr PoP into the caller's `BatchVerifier` (`batch_id` = `BatchId::Decryption(l)`).
2. `ecdh(&self.enc_key, msg.key)` — computes the static-DH value `b·K` where `b` is the recipient's registered encryption key and `K = msg.key` is fully attacker-controlled.
3. Applies the keystream and returns `(msg.msg, EncryptionKeyProof { key: b·K, dleq })`.

No verification result is known at the time the proof is produced. [1](#0-0) 

In `crypto/dkg/pedpop/src/lib.rs`, `calculate_share` then:

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
```

If the decrypted bytes are not a canonical scalar, the function returns `InvalidShare` carrying the `EncryptionKeyProof` — containing `proof.key = b·K` plus a DLEq proof that it is the correct ECDH for `K` against the recipient's registered `enc_pub_key`. The batch verification that would have caught the missing PoP is only reached later at `batch.verify_with_vartime_blame()`, which this early `?` bypasses. [2](#0-1) 

The PoP exists precisely to prevent "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X ... they'd reveal bX, revealing Alice's message to Bob" — the comment in `encryption.rs` describes this exact attack, but the early error path re-opens it because the blame proof is emitted before the PoP is verified. [3](#0-2) 

### Impact Explanation
An unprivileged participant in a PedPoP DKG session can:

1. Intercept/copy `key` (= `K_victim = g·k_victim`) from any honest sender's `EncryptedMessage` addressed to the target recipient.
2. Submit their own `EncryptedMessage` with `key = K_victim`, a garbage `pop`, and a `msg` body that will not deserialize to a canonical `C::F` (e.g., all-`0xff` bytes).
3. `calculate_share` returns `PedPoPError::InvalidShare { participant: attacker, blame: Some(proof) }` where `proof.key = enc_key_recipient · K_victim` — exactly the shared ChaCha20 key protecting the victim's message.
4. The attacker re-derives the cipher via `cipher(context, proof.key)` and decrypts the victim's ciphertext, recovering a secret share of the DKG.

This is key-share material exposure reachable purely with public-protocol inputs (`EncryptedMessage` fields), caused by a decryption-oracle/policy-bypass ordering bug identical in class to the mbedtls PSA issue (cryptographic output produced and exposed before the enforcing check).

### Likelihood Explanation
Any participant who can deliver a secret-share message in the DKG round can trigger it deterministically — no probability, no collusion, no malformed curve points required (the `key` field just needs to be a valid non-identity point, which the copied `K_victim` is). The only requirement is that the surrounding protocol surfaces the `blame` field of `PedPoPError::InvalidShare` to the sender; the blame mechanism exists precisely so this proof is transmitted for arbitration, so the value is designed to be published.

### Recommendation
Verify `msg.pop` synchronously inside `Encryption::decrypt` (or return a proof type that is only constructed/releasable after `batch.verify_with_vartime_blame()` confirms the `BatchId::Decryption(l)` entry). Concretely, `decrypt` should not emit an `EncryptionKeyProof` for a message whose PoP has not yet been verified; alternatively, defer PoP failures to be resolved before any `InvalidShare` error carrying `blame` is returned from `calculate_share`.

### Proof of Concept
```rust
// Attacker (participant A) toward victim-recipient (participant B) in a DKG round.
// 1. B registered enc_pub_key E = g*b. Honest sender S sent B EncryptedMessage
//    { key: K_s = g*k_s, pop, msg: Enc(share_S->B) }.
// 2. A builds their own share message to B:
//      msg.key   = K_s                      // copied; A does NOT know k_s
//      msg.pop   = SchnorrSignature::sign(&a, r, challenge(...)) // any valid sig over A's own key won't matter; garbage works too
//      msg.msg   = SecretShare([0xff; 32])  // non-canonical scalar bytes
// 3. B calls calculate_share(..). Inside decrypt():
//      - pop is only queued into `batch` (never verified before return)
//      - key = ecdh(b, K_s) = b*K_s         // the shared key of S's message!
//      - keystream applied; from_repr fails
//      - calculate_share returns Err(InvalidShare { participant: A, blame: Some(proof) })
//        with proof.key = b*K_s and a valid DLEq tying it to E and K_s.
// 4. A computes cipher::<C>(context, &proof.key) and applies its keystream to
//    S's ciphertext, recovering S's secret share to B in the clear —
//    despite A never being able to produce the required proof of possession for K_s.
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
