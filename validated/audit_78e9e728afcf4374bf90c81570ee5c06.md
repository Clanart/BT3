### Title
PedPoP registers participant encryption keys without proof-of-possession, allowing key co-option and recovery of another participant's secret share - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The DKG's `EncryptionKeyMessage` carries a long-term `enc_key` public key per participant, but `Decryption::register` inserts whatever key the sender declares with no proof-of-possession and no uniqueness check across participants. A participant Eve can therefore register honest Alice's `enc_pub_key` as her own. Every secret share addressed to Eve's `Participant` index is then encrypted to a key only Alice holds, letting Alice decrypt all shares `f_j(Eve)` and reconstruct Eve's aggregate secret share — a secret she was never authorized to possess. This is the "insufficiently protected credentials / missing authorization check" analog: a credential (the encryption key) is accepted on unauthenticated declaration, and that missing check yields disclosure of protected secret material.

### Finding Description
`Encryption::registration` emits `EncryptionKeyMessage { msg, enc_key }` where `enc_key` is a bare `C::G` group element with no attached Schnorr proof [1](#0-0) . On receipt, `Decryption::register` only asserts the *same* participant didn't re-register, then inserts `msg.enc_key` verbatim [2](#0-1) . Nothing proves the registrant knows the discrete log of `enc_key`, and nothing prevents two distinct `Participant` indexes from registering the same key.

Senders encrypt shares via `encrypt(..., to = decryption.enc_keys[&participant], msg)`, i.e., to the *registered* key of the destination index [3](#0-2) . The per-message PoP in `EncryptedMessage` only binds the ephemeral per-message key to the sender — it says nothing about the recipient's registered key [4](#0-3) . The spec's stated defense against key co-option ("a proof-of-possession is also included with encrypted messages") addresses Eve *reusing a per-message key*, not Eve *registering a victim's static encryption key*, which is the attack surface left open [5](#0-4) .

Downstream, `KeyMachine::calculate_share` decrypts each received message under the receiver's own `enc_key` [6](#0-5) , and `decrypt_with_proof` in the blame path verifies the DLEq against `self.enc_keys[&decryptor]` — the registered (co-opted) key — so blame machinery also treats the co-opted key as legitimate [7](#0-6) .

### Impact Explanation
Each share `f_j(i)` a sender `j` produces for index `i` is the confidential input to `i`'s final threshold key share; `calculate_share` sums them into `self.secret` [8](#0-7) . If Eve registers Alice's `enc_pub_key`, then for every sender `j`, the message intended for `i = Eve` decrypts under Alice's private `enc_key`. Alice learns all `{f_j(Eve)}` and computes `s_Eve = Σ_j f_j(Eve)` — Eve's complete secret share. Alice thereby holds shares for two indexes, collapsing the effective threshold (a t-of-n scheme where one attacker controls two shareholders' material), and obtains a verification-share-consistent share she was never entitled to. Additionally, if Eve publishes blame for undecryptable messages, the released `EncryptionKeyProof` ECDH keys let *all* observers decrypt those shares, widening the disclosure [9](#0-8) .

### Likelihood Explanation
The attacker needs only to be a DKG participant broadcasting an `EncryptionKeyMessage` — a normal protocol message with public inputs, no special privileges. The attack is deterministic: registering `enc_key = victim's enc_pub_key` is accepted unconditionally since `register` performs no PoP check and no cross-participant duplicate check [2](#0-1) . It is a Medium-severity analog consistent with the referenced class (missing check on a credential → disclosure of protected secrets).

### Recommendation
Bind `enc_key` to its registrant with a proof-of-possession: include a Schnorr signature/PoK over the transcript context, the participant index, and `enc_key` inside `EncryptionKeyMessage`, verified in `Decryption::register`/`Encryption::register` before inserting into `enc_keys`. Additionally enforce uniqueness of `enc_key` across all participants (reject a key already registered to a different index), so co-option is rejected outright rather than merely unverifiable.

### Proof of Concept
Conceptual, in-scope (`crypto/dkg/pedpop`):

```rust
// In a t-of-n PedPoP DKG with participants {1..=n}:
// 1. Honest participant Alice (index a) broadcasts her EncryptionKeyMessage
//    containing enc_pub_key_A = G * enc_key_A.
// 2. Malicious participant Eve (index e) constructs her own
//    EncryptionKeyMessage { msg: Commitments<..>, enc_key: enc_pub_key_A }
//    — copying Alice's key. Decryption::register(e, msg) accepts it because
//    it only asserts !enc_keys.contains_key(&e).
// 3. Every sender j calls encrypt(rng, ctx, j, enc_keys[&e] == enc_pub_key_A,
//        share f_j(e)).
//    The resulting ECDH is enc_key_A * per_message_key — computable by Alice.
// 4. Alice observes/relays the ciphertexts addressed to e (or receives them
//    via blame proofs), decrypts each with her own enc_key_A, and recovers
//    s_e = Σ_j f_j(e) — Eve's secret share — without Eve's cooperation.
// 5. Eve cannot decrypt her own shares (she lacks enc_key_A); if she files
//    blame, the published EncryptionKeyProof reveals the ECDH key publicly,
//    leaking s_e's components to all observers.
```

The fix's absence is directly visible: `EncryptionKeyMessage` serialization carries only `msg` + `enc_key` with no signature field [10](#0-9) , and `register` performs no verification [2](#0-1) .

*Uncertainty note:* index coverage of `EncryptionKeyMessage`'s definition in `lib.rs` was limited (grep confirmed its fields but not whether a companion PoP field exists elsewhere in the struct). If `EncryptionKeyMessage` bundles a PoP on `enc_key` that `register` callers verify upstream, this finding would not hold; from all reachable code paths examined, no such verification exists at the point of registration.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L448-458)
```rust
  pub(crate) fn registration<M: Message>(&self, msg: M) -> EncryptionKeyMessage<C, M> {
    EncryptionKeyMessage { msg, enc_key: self.enc_pub_key }
  }

  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    self.decryption.register(participant, msg)
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

**File:** spec/cryptography/Distributed Key Generation.md (L19-35)
```markdown
### Blame

Since each message has a distinct key attached, and accordingly a distinct
shared key, it's possible to reveal the shared key for a specific message
without revealing any other message's decryption keys. This is utilized when a
participant misbehaves. A participant who receives an invalid encrypted message
publishes its key, able to without concern for side effects, With the key
published, all participants can decrypt the message in order to decide blame.

While key reuse by a participant is considered as them revealing the messages
themselves, and therefore out of scope, there is an attack where a malicious
adversary claims another participant's encryption key. They'll fail to encrypt
their message, and the recipient will issue a blame statement. This blame
statement, intended to reveal the malicious adversary, also reveals the message
by the participant whose keys were co-opted. To resolve this, a
proof-of-possession is also included with encrypted messages, ensuring only
those actually with per-message keys can claim to use them.
```

**File:** crypto/dkg/pedpop/src/lib.rs (L476-492)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L575-609)
```rust
  fn blame_internal(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };

    let Some(share) = Option::<C::F>::from(C::F::from_repr(share_bytes.0)) else {
      // If this isn't a valid scalar, the sender is faulty
      return sender;
    };

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

    // The share was canonical and valid
    recipient
  }
```
