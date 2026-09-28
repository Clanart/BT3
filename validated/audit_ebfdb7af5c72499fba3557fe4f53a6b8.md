### Title
Unauthenticated ECDH encryption key enables MITM substitution and full group secret recovery - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The DKG's round-1 `EncryptionKeyMessage` carries a per-participant ECDH public key (`enc_key`) that is never bound to the sender's proof-of-knowledge. The Schnorr PoK verified in `verify_r1` signs only the serialized `Commitments` (`cached_msg`), not `enc_key`. An attacker in a privileged network position (the exact bug class of the lix advisory: unauthenticated channel / redirect) can replace `enc_key` in any participant's broadcast with a key they control. Every other participant then encrypts that victim's FROST secret share to the attacker's key via `encrypt`/`ecdh`. The attacker decrypts all shares and reconstructs the threshold group's secret key — full key recovery by a non-participant.

### Finding Description
`EncryptionKeyMessage::read` deserializes `msg` (the `Commitments`, which carry the PoK) and `enc_key` as two independent, concatenated fields (crypto/dkg/pedpop/src/encryption.rs:57-58). [1](#0-0) 

In `verify_r1`, the only authentication performed is batch-verifying `msg.sig` against `msg.commitments[0]` with challenge `challenge::<C>(context, l, msg.sig.R, &msg.cached_msg)` — `cached_msg` covers only the `Commitments` serialization, not the trailing `enc_key` bytes (crypto/dkg/pedpop/src/lib.rs:323-329). [2](#0-1) 

The unauthenticated `enc_key` is then stored per-participant in `Decryption::register` (crypto/dkg/pedpop/src/encryption.rs:351-361). [3](#0-2) 

When shares are generated, `Encryption::encrypt` looks up `self.decryption.enc_keys[&participant]` and calls `encrypt`, which computes `ecdh::<C>(&key, to)` — ECDH between a fresh ephemeral scalar and the attacker-supplied `enc_key` — and XORs the share bytes with the derived ChaCha20 keystream (crypto/dkg/pedpop/src/encryption.rs:466, 154). [4](#0-3) [5](#0-4) 

The per-message PoP in `pop_challenge` binds `context`, `nonce`, the ephemeral `key`, `sender`, and the ciphertext — but not the recipient's `enc_key` either (crypto/dkg/pedpop/src/encryption.rs:302-323), so it does not detect the substitution. [6](#0-5) 

### Impact Explanation
A MITM who rewrites `enc_key` for participant `l` learns `share_i→l` from every sender `i`, hence learns `l`'s final secret share. Swapping `enc_key` for all `n` participants yields every sent share `f_i(j)`, from which the attacker computes every participant's final share and Lagrange-interpolates the group secret key — complete key recovery by someone who is not even a DKG participant, analogous to lix redirecting a download to attacker-controlled bytes. Honest recipients later fail `from_repr`/`share_verification_statements` and trigger blame (calculate_share, lib.rs:479-499), but the key is already compromised; the leak happens before any detection. [7](#0-6) 

### Likelihood Explanation
The code explicitly assumes an external authenticated channel ("The per-message keys have no root of trust other than their existence in the assumed-to-exist external authenticated channel", encryption.rs:300-301), so exploitation requires the MITM network position the bug class presumes — matching the lix advisory's `AV:N/AC:H`. No collusion or malicious validator is needed. Any deployment where the round-1 commitments are relayed over a channel that does not authenticate the full byte string (e.g., authenticating only the commitments payload, or a compromised/reordered relay) is exposed.

### Recommendation
Bind `enc_key` into the sender's authentication: include `enc_key.to_bytes()` in the PoK challenge (extend `challenge::<C>`/`cached_msg` coverage in `verify_r1` so the signature commits to the whole `EncryptionKeyMessage` serialization), or sign `enc_key` with the participant's FROST/session identity key. Alternatively, hash the full received message into `context` so substituted keys produce a transcript mismatch.

### Proof of Concept
1. Participants `1..=n` run `KeyGenMachine::generate_coefficients` and broadcast `EncryptionKeyMessage { msg: Commitments, enc_key }`.
2. MITM intercepts participant `l`'s message, replaces `enc_key` with `K_a = a·G` (attacker scalar `a`), re-serializes via `EncryptionKeyMessage::write`, and forwards. The PoK inside `msg` is untouched, so `verify_r1` accepts it.
3. Each sender `i` calls `generate_secret_shares`; internally `encrypt(rng, l, share_i→l)` computes `shared = a·(k·G)` where `k` is the ephemeral key in `msg.key`, and produces `EncryptedMessage { key: k·G, pop, msg: ct }`.
4. MITM observes the forwarded `EncryptedMessage`, computes `shared = a·msg.key`, runs `cipher(context, shared)`, and `apply_keystream` on `msg` to recover `SecretShare` plaintext — identical to the derivation in `Encryption::decrypt` (encryption.rs:487-488).
5. After all n² shares are recovered, MITM sums shares per recipient to get each participant's final share `s_j`, then Lagrange-interpolates any `t` of them to obtain the group secret key.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L151-156)
```rust
  // Generate a new key for this message, satisfying cipher's requirement of distinct keys per
  // message, and enabling revealing this message without revealing any others
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L323-329)
```rust
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );
```

**File:** crypto/dkg/pedpop/src/lib.rs (L476-491)
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
```
