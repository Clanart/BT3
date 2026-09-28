### Title
Unauthenticated malleable DKG share channel lets a network MITM get an honest participant fatally slashed - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP DKG encrypts secret shares with a bare ChaCha20 stream cipher (no MAC) and intentionally provides no authenticated channel. The Schnorr proof-of-possession binds the per-message key to the ciphertext bytes, so a man-in-the-middle who flips even one bit of a share in transit produces a message whose PoP fails verification. All blame paths then attribute the fault to the honest *sender*, and the processor treats that attribution as fatal slashing. An unprivileged network attacker with no protocol key material can therefore cause arbitrary honest participants to be blamed and slashed, and abort the key generation.

### Finding Description
`cipher` constructs `ChaCha20` directly and applies a raw keystream — encryption without integrity. [1](#0-0)  `encrypt` XORs the plaintext share with this keystream, emitting `EncryptedMessage { key, pop, msg }` where `msg` is unauthenticated ciphertext. [2](#0-1) 

The only integrity check is `pop`, a Schnorr signature over `pop_challenge`, which does include the ciphertext via `transcript.append_message(b"message", msg)` — but a failure of that signature is treated as proof the *sender* misbehaved, not as evidence of transport tampering. [3](#0-2)  The code itself acknowledges the gap: "This still doesn't mean the DKG offers an authenticated channel. The per-message keys have no root of trust other than their existence in the assumed-to-exist external authenticated channel." [4](#0-3) 

On the receive path, `EncryptedMessage::read` accepts attacker-controlled bytes. [5](#0-4)  `KeyMachine::calculate_share` then queues the PoP under `BatchId::Decryption(l)`; when it fails, it returns `PedPoPError::InvalidShare { participant: l, blame: None }`, naming the sender as the faulty party. [6](#0-5) 

If the dispute escalates to `BlameMachine::blame` / `AdditionalBlameMachine::blame`, `blame_internal` calls `decrypt_with_proof`, which returns `DecryptionError::InvalidSignature` for the tampered ciphertext — and `blame_internal` returns `sender`. [7](#0-6) [8](#0-7) 

In the processor, this propagates to `ProcessorMessage::InvalidShare { faulty: i }` / `ProcessorMessage::Blame { participant: accused }`, where the surrounding comment confirms the consequence: "this being called means *someone* is getting fatally slashed." [9](#0-8) [10](#0-9) 

A complementary malleability path exists even where a blame proof *is* produced: since ChaCha20 is a stream cipher, XOR-ing a delta into the ciphertext XORs the same delta into the decrypted scalar. The tampered share fails `share_verification_statements`, the recipient publishes the ECDH blame key, and `blame_internal` again returns `sender` because the decrypted value does not match the sender's commitments — framing the honest sender for transport corruption.

### Impact Explanation
This is the direct analog of "hardcoded insecure TLS": the protocol layer relies on a confidential-but-unauthenticated channel, so a MITM needs no credentials, key shares, or validator status. The concrete harms:

- **False blame / slashing of honest parties.** Every tampering variant (ciphertext bit-flip, truncated message, replayed/misrouted share) resolves to `sender` in `blame_internal`, and the processor's comment states such blame is fatal ("getting fatally slashed"). A network attacker can therefore get honest validators slashed at will.
- **Forced aborts / targeted exclusion.** `calculate_share` returns an error on the first failed PoP, aborting the victim's `KeyMachine`. Repeatable each attempt, allowing an attacker to censor specific participants from ever joining a key set.
- No impact requires compromising a participant; only byte-level control of `EncryptedMessage` bytes in transit, which the in-scope rules explicitly treat as reachable via `EncryptedMessage::read` / `calculate_share`.

### Likelihood Explanation
The attacker model matches the advisory exactly: a MITM on the transport between DKG participants (or a malicious relay/coordinator forwarding shares). No cryptographic break, no collusion, and no validator key is required — the attacker only modifies bytes. Any routing infrastructure, compromised coordinator, or on-path adversary suffices. Because `context` is fixed per session and encryption keys are ephemeral per message, the attacker cannot forge a *valid* message, but does not need to: the failure mode itself is weaponized against the sender. Severity: High — integrity of honest-participant slashing decisions is compromised by an unprivileged party.

### Recommendation
- Add a MAC to each `EncryptedMessage` (e.g., derive a second key from the `cipher` transcript — `transcript.challenge(b"mac")` — and compute keyed Blake2b/HMAC over `key || msg`), verified in `decrypt` and `decrypt_with_proof` before PoP evaluation.
- Alternatively switch to an AEAD (ChaCha20-Poly1305) keyed by the ECDH-derived key with `from || to || context` as associated data.
- Distinguish transport-corruption from sender-fault: a MAC failure should be attributed to neither participant (retry/abort without blame), and `blame_internal` should not return `sender` for authentication failures that the sender could not have caused once a valid PoP exists.
- Document the remaining reliance on an authenticated broadcast channel for sender attribution (the PoP binds `sender` only into the challenge; the transport must still authenticate who sent which bytes).

### Proof of Concept
```
1. n-of-t PedPoP session. Honest Alice (sender l) produces
   EncryptedMessage { key = X, pop = sig over challenge(context, R, X, alice, C),
                      msg = C = share XOR keystream } for Bob.
2. MITM replaces C with C' = C XOR delta (or truncates the byte stream) before Bob's
   EncryptedMessage::read / calculate_share.
3. Bob's decrypt() queues pop verification with pop_challenge(..., msg = C'), which fails
   because Alice signed over C. batch fails on BatchId::Decryption(alice).
4. calculate_share -> Err(InvalidShare { participant: alice, blame: None }).
   Processor emits InvalidShare { faulty: alice }.
5. Escalation: VerifyBlame -> AdditionalBlameMachine::blame -> decrypt_with_proof
   -> pop.verify fails -> InvalidSignature -> blame_internal returns `alice`.
6. Result: Alice — who sent a perfectly valid share — is identified faulty and,
   per processor/src/key_gen.rs commentary, fatally slashed; the session aborts.
   Repeatable against any participant on every attempt.
```
The same flow with a valid-PoP malleation is impossible (PoP covers `msg`), but it is unnecessary: the tamper-induced `InvalidSignature` path already yields maximal harm — attribution of a network fault to an honest signer.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L153-167)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L299-301)
```rust
// This doesn't need to take the msg. It just doesn't hurt as an extra layer.
// This still doesn't mean the DKG offers an authenticated channel. The per-message keys have no
// root of trust other than their existence in the assumed-to-exist external authenticated channel.
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
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
    } else {
      Err(DecryptionError::InvalidProof)
    }
  }
```

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

**File:** crypto/dkg/pedpop/src/lib.rs (L575-608)
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
```

**File:** processor/src/key_gen.rs (L402-430)
```rust
          // Parse the shares
          let mut shares = HashMap::new();
          for i in 1 ..= params.n() {
            let i = Participant::new(i).unwrap();
            let Some(share) = shares_ref.get_mut(&i) else { continue };
            shares.insert(
              i,
              EncryptedMessage::<C, SecretShare<C::F>>::read(share, params).map_err(|_| {
                ProcessorMessage::InvalidShare { id, accuser: params.i(), faulty: i, blame: None }
              })?,
            );
          }

          Ok(
            (match machine.calculate_share(rng, shares) {
              Ok(res) => res,
              Err(e) => match e {
                PedPoPError::InvalidShare { participant, blame } => {
                  Err(ProcessorMessage::InvalidShare {
                    id,
                    accuser: params.i(),
                    faulty: participant,
                    blame: Some(blame.map(|blame| blame.serialize())).flatten(),
                  })?
                }
                _ => panic!("unknown error: {e:?}"),
              },
            })
            .complete(),
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
