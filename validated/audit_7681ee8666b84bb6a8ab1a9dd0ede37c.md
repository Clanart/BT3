### Title
Encrypted DKG secret shares are not bound to their intended recipient, enabling message redirection that frames honest participants — (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Frappe report describes an authorization artifact (an OAuth consent/token) that is not scoped to the requesting client, letting it be replayed for the wrong party. The direct analog in Serai is `EncryptedMessage` in PedPoP: the per-message encryption key's proof of possession binds the context, the sender, the key, and the ciphertext — but never the intended recipient. An `EncryptedMessage` produced for participant Bob is a fully valid, PoP-verifying message when presented to participant Carol, where it can only produce a garbage share and an incorrect blame verdict against the honest sender.

### Finding Description
`encrypt` derives the message key via ECDH between a fresh per-message key and the recipient's registered encryption key, then signs a Schnorr PoP over `pop_challenge(context, nonce, pub_key, from, msg)` — the recipient identity (`to`) is never transcripted [1](#0-0) . Likewise `pop_challenge` appends `context`, `nonce`, `key`, `sender`, and `message` only [2](#0-1) . On the receive side, `decrypt`/`decrypt_with_proof` verify the PoP against `msg.key` and the claimed `from`, then ECDH with *whichever* decryption key the local `Decryption` box holds [3](#0-2) .

Because nothing cryptographically binds the intended recipient, any party that obtains the wire bytes of `EncryptedMessage` (it is transmitted per-recipient but is not secret — it is a broadcast-able, self-authenticating object) can deliver it to a different participant Carol as "sender Alice's share for you." The PoP verifies (it is genuinely Alice's signature over the real key and ciphertext), the ECDH against Carol's `enc_key` produces a wrong cipher key, the plaintext is garbage, and `blame_internal` then attributes fault to `sender` — the honest Alice — since the deserialized share fails `from_repr` or `share_verification_statements` [4](#0-3) . Carol's `EncryptionKeyProof` reveals her own ECDH key for `msg.key`, not Bob's, so the redirection costs the attacker nothing.

### Impact Explanation
An unprivileged party who can copy/route DKG round-2 messages (any other participant, or anyone observing the authenticated channel) can cause an honest participant to be adjudicated faulty and the key-generation session aborted. Since blame is unconditional once the redirected message is processed, this is a framing/liveness attack reachable purely from public message bytes, matching the Frappe pattern of an authorization artifact being honored for a party it was never issued to.

### Likelihood Explanation
The attack requires only relaying existing bytes to a different recipient — no forgery, no key knowledge, no collusion. Any participant or network observer in the DKG can perform it. It does not leak Alice's or Bob's secret share (the revealed ECDH key decrypts only for Carol's key), so the ceiling is Medium: false blame attribution plus forced abort, not key compromise.

### Recommendation
Bind the recipient into the per-message authorization: append `to`'s `Participant` index (or registered `enc_key`) in `pop_challenge` in `crypto/dkg/pedpop/src/encryption.rs`, and have `decrypt`/`decrypt_with_proof` recompute the challenge with the local participant index (mirroring how `from` is bound), so a redirected message fails PoP verification before any ECDH/blame path is reached.

### Proof of Concept
1. Run `KeyGenMachine::generate_coefficients`/`generate_secret_shares` for participants 1..=n as in `commit_enc_keys_and_shares` [5](#0-4) .
2. Take the serialized `EncryptedMessage` Alice produced for Bob (`secret_shares[&ALICE][&BOB]`); deliver those exact bytes to Carol's `KeyMachine::calculate_share` as Alice's share for Carol.
3. The PoP in `decrypt` verifies because `pop_challenge` excludes the recipient; decryption via Carol's `enc_key` yields garbage.
4. Carol emits blame; `BlameMachine::blame` calls `decrypt_with_proof` — PoP passes, DLEq for Carol's key passes, plaintext share is invalid — and `blame_internal` returns `sender` (Alice), marking an honest participant faulty and aborting the protocol.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L135-168)
```rust
fn encrypt<R: RngCore + CryptoRng, C: Ciphersuite, E: Encryptable>(
  rng: &mut R,
  context: [u8; 32],
  from: Participant,
  to: C::G,
  mut msg: Zeroizing<E>,
) -> EncryptedMessage<C, E> {
  /*
  The following code could be used to replace the requirement on an RNG here.
  It's just currently not an issue to require taking in an RNG here.
  let last = self.last_enc_key.to_bytes();
  self.last_enc_key = C::hash_to_F(b"encryption_base", last.as_ref());
  let key = C::hash_to_F(b"encryption_key", last.as_ref());
  last.as_mut().zeroize();
  */

  // Generate a new key for this message, satisfying cipher's requirement of distinct keys per
  // message, and enabling revealing this message without revealing any others
  let key = Zeroizing::new(C::random_nonzero_F(rng));
  cipher::<C>(context, &ecdh::<C>(&key, to)).apply_keystream(msg.as_mut().as_mut());

  let pub_key = C::generator() * key.deref();
  let nonce = Zeroizing::new(C::random_nonzero_F(rng));
  let pub_nonce = C::generator() * nonce.deref();
  EncryptedMessage {
    key: pub_key,
    pop: SchnorrSignature::sign(
      &key,
      nonce,
      pop_challenge::<C>(context, pub_nonce, pub_key, from, msg.deref().as_ref()),
    ),
    msg,
  }
}
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-397)
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
      Ok(msg.msg)
    } else {
      Err(DecryptionError::InvalidProof)
    }
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L582-608)
```rust
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
```

**File:** crypto/dkg/pedpop/src/tests.rs (L29-80)
```rust
#[allow(clippy::type_complexity)]
fn commit_enc_keys_and_shares<R: RngCore + CryptoRng, C: Ciphersuite>(
  rng: &mut R,
) -> (
  HashMap<Participant, KeyMachine<C>>,
  HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  HashMap<Participant, C::G>,
  HashMap<Participant, PedPoPSecretShares<C>>,
) {
  let mut machines = HashMap::new();
  let mut commitments = HashMap::new();
  let mut enc_keys = HashMap::new();
  for i in (1 ..= PARTICIPANTS).map(|i| Participant::new(i).unwrap()) {
    let params = ThresholdParams::new(THRESHOLD, PARTICIPANTS, i).unwrap();
    let machine = KeyGenMachine::<C>::new(params, CONTEXT);
    let (machine, these_commitments) = machine.generate_coefficients(rng);
    machines.insert(i, machine);

    commitments.insert(
      i,
      EncryptionKeyMessage::read::<&[u8]>(&mut these_commitments.serialize().as_ref(), params)
        .unwrap(),
    );
    enc_keys.insert(i, commitments[&i].enc_key());
  }

  let mut secret_shares = HashMap::new();
  let machines = machines
    .drain()
    .map(|(l, machine)| {
      let (machine, mut shares) =
        machine.generate_secret_shares(rng, clone_without(&commitments, &l)).unwrap();
      let shares = shares
        .drain()
        .map(|(l, share)| {
          (
            l,
            EncryptedMessage::read::<&[u8]>(
              &mut share.serialize().as_ref(),
              // Only t/n actually matters, so hardcode i to 1 here
              ThresholdParams::new(THRESHOLD, PARTICIPANTS, Participant::new(1).unwrap()).unwrap(),
            )
            .unwrap(),
          )
        })
        .collect::<HashMap<_, _>>();
      secret_shares.insert(l, shares);
      (l, machine)
    })
    .collect::<HashMap<_, _>>();

  (machines, commitments, enc_keys, secret_shares)
```
