### Title
Missing recipient binding in PedPoP encrypted-share authentication lets an attacker replay an honest participant's ciphertext and get them blamed - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
Analogous to CVE-2019-20143 (incorrect access control), the DKG encryption layer fails to bind an `EncryptedMessage` to its intended recipient. The per-message proof-of-possession (`pop_challenge`) and the ECDH/cipher transcript authenticate the *sender* and the *message bytes*, but never the recipient. An unprivileged participant can therefore take an authentic encrypted share a victim sent to a *different* participant, replay it as the share addressed to themselves, and — through the blame mechanism — have the fully honest victim adjudicated faulty and slashed.

### Finding Description
When encrypting a secret share, `encrypt` computes the PoP challenge over `(context, nonce, key, sender, msg)` — the recipient's identity and their registered encryption public key are absent. [1](#0-0) 

Consequently, a ciphertext produced by sender `B` for recipient `C` is a perfectly well-authenticated message "from `B`" when presented as "to `A`". The documented blame contract only requires that "This message must have been authenticated as actually having come from the sender in question" — a replayed message satisfies that. [2](#0-1) 

In `blame_internal`, `decrypt_with_proof(sender, recipient, msg, proof)` verifies:
1. `msg.pop` — passes, since the challenge doesn't cover the recipient. [3](#0-2) 
2. The accuser's DLEq proof — passes, since it correctly proves `ecdh(A_enc_key, msg.key)` against `enc_keys[&decryptor]` (the accuser's own registered key). [4](#0-3) 

The decrypted bytes are `ChaCha20(a·k·G) ⊕ share_to_C` — garbage, but with overwhelming probability still a canonical scalar (any 32-byte string `< group order` parses via `from_repr`, and for Ristretto/Ed25519 almost all byte strings are canonical). The share-consistency check against `self.commitments[&sender]` then fails, and `blame_internal` returns `sender`. [5](#0-4) 

The same applies to `calculate_share`: A injects B's share-to-C ciphertext keyed under B in the `shares` map (it passes `validate_map` since the key is B's index); the PoP batch-verifies because the sender field is genuinely B. [6](#0-5) 

### Impact Explanation
An attacker participant `A` causes an honest participant `B` to be cryptographically "proven" faulty. In Serai's deployment, `ProcessorMessage::Blame`/VerifyBlame resolves `blame()` output into a fatal slash of the blamed validator — so this is a wrongful-slashing primitive reachable entirely with data the attacker legitimately possesses (an authentic ciphertext B broadcast for another recipient). It also lets a malicious recipient falsely accuse honest senders to abort/split the DKG with blame misattributed.

### Likelihood Explanation
Any DKG participant can perform this: they only need the encrypted share message the victim sent to someone else (available to anyone who observes or relays DKG traffic, and to the coordinator routing shares). No key compromise, collusion, or timing is required; success is deterministic up to the negligible chance the garbage plaintext isn't a canonical scalar (in which case `blame_internal` *still* returns `sender`, since an unparseable scalar also blames the sender — line 592).

### Recommendation
Bind the intended recipient into the per-message authentication. In `encrypt`/`pop_challenge`, append the recipient's `Participant` index and/or the recipient's registered `enc_key` (`transcript.append_message(b"recipient", ...)`), and in `decrypt_with_proof`/`decrypt`, recompute the challenge with the claimed recipient so a ciphertext only authenticates as "from `sender` to `recipient`". This makes replay-across-recipients produce `DecryptionError::InvalidSignature` — which correctly blames the message as malformed for this claim rather than condemning the sender's genuine share.

### Proof of Concept
1. Honest `B` runs `generate_secret_shares`, producing `enc_msg_bc = EncryptedMessage` addressed to `C` (ECDsa key `ecdh(k, enc_pub_C)`), with PoP over `(context, R, msg.key, B, msg)`.
2. Attacker `A` obtains `enc_msg_bc` (shares are routed/relayed; A only needs a copy).
3. In `A`'s `calculate_share`, A inserts `enc_msg_bc` under key `B` in `shares`. `validate_map` accepts it; `msg.pop.batch_verify` passes (challenge uses `from = B`, matching B's signature).
4. `A` decrypts with `a·k·G` → garbage scalar `x`; `share_verification_statements(i=A, commitments[B], x)` fails → `PedPoPError::InvalidShare { participant: B }` — B is framed.
5. Alternatively A escalates via blame: publishes `proof = EncryptionKeyProof { key: a·k·G, dleq }` which verifies against `enc_keys[A]`. `blame_internal` returns `sender = B` (share parseable but inconsistent) → B is slashed despite having behaved honestly.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L302-323)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-392)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L595-605)
```rust
    // If this isn't a valid share, the sender is faulty
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
    }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L615-618)
```rust
  /// The message should be a copy of the encrypted secret share from the accused sender to the
  /// accusing recipient. This message must have been authenticated as actually having come from
  /// the sender in question.
  ///
```
