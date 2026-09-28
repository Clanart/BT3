### Title
Forged encrypted share triggers publication of an ECDH blame key that decrypts an honest party's message - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`KeyMachine::calculate_share` returns the recipient's `EncryptionKeyProof` (the ECDH shared key `bX` plus a DLEq proof) as soon as the decrypted bytes fail canonical scalar decoding, before the batched proof-of-possession (PoP) for that message's ephemeral key is ever verified. A malicious sender can copy the per-message key `X` from a victim's `EncryptedMessage` addressed to the same recipient, wrap arbitrary garbage under it, and induce the honest recipient to publish a valid `EncryptionKeyProof` for `bX`. That published key is exactly the ChaCha20 key stream seed for the victim's ciphertext, so everyone (including the attacker) can decrypt the victim's secret share — precisely the side effect the PoP in `EncryptedMessage` was added to prevent. [1](#0-0) [2](#0-1) 

### Finding Description
In `encrypt`, each `EncryptedMessage` carries a fresh ephemeral key `key`, its public point `pub_key = G*key` stored as `msg.key`, and a Schnorr PoP over `(context, pub_nonce, pub_key, from, ciphertext)` proving ownership of `key`. [3](#0-2)  The comment explicitly documents that without the PoP, an adversary reusing key `X` would cause the recipient's blame to reveal `bX`, decrypting the co-opted victim's message. [4](#0-3) 

`Encryption::decrypt` computes `key = ecdh(enc_key, msg.key)` and unconditionally constructs the `EncryptionKeyProof { key, dleq }`, while the PoP is only *queued* into a `BatchVerifier`. [5](#0-4)  Back in `calculate_share`, `C::F::from_repr(share_bytes.0)` is evaluated inline, and on failure the function immediately returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }` — the batch (which is the only place the PoP is checked via `BatchId::Decryption`) is never awaited on this path. [6](#0-5)  The processor serializes and propagates this blame in `ProcessorMessage::InvalidShare`. [7](#0-6) 

The DLEq inside the proof verifies against `(G, msg.key)` and `(enc_keys[decryptor], proof.key)` — it does not depend on the PoP at all — so the leaked `proof.key = bX` is unconditionally usable as `cipher(context, bX)` keystream against any ciphertext whose `msg.key == X`. [8](#0-7) 

### Impact Explanation
Unintended disclosure of a confidential DKG secret share. Eve copies the public `msg.key = X` from honest Alice's `EncryptedMessage` to Bob (transmitted over authenticated-but-readable channels), encrypts garbage under `X`, and sends it to Bob as her own share. Bob's node decrypts to non-canonical bytes and emits `blame = Some(EncryptionKeyProof)` containing `bX`. Anyone observing the blame can run `cipher::<C>(context, &bX)` over Alice's ciphertext and recover Alice's secret share `s_alice(bob)`. Repeated across `t` victims' blame events, Eve reconstructs Alice's full sharing polynomial and her secret DKG contribution, weakening the generated threshold key. This is an unprivileged-party-reachable information disclosure of secret key material, mirroring the report's CWE-200 class.

### Likelihood Explanation
Reachable entirely with public inputs: the attacker only needs to observe a victim's `EncryptedMessage.key` (public bytes), then feed crafted bytes to `EncryptedMessage::read` / `calculate_share` — both enumerated reachable paths. The only requirement is that the decrypted garbage fails `from_repr`, which holds with overwhelming probability for random ciphertext. The gap exists because the canonicality early-return predates `batch.verify_with_vartime_blame`. [9](#0-8)  Exploitation further requires each targeted recipient to propagate the blame (the processor does so automatically in `ProcessorMessage::InvalidShare`), so practical impact scales with how blame messages are broadcast; per-incident it reliably leaks one share. Medium.

### Recommendation
Do not attach the `EncryptionKeyProof` to an error until the PoP batch has been verified for that message. Specifically, in `calculate_share`, treat a non-canonical decrypted share as a `BatchId::Decryption`-class failure (blame `None`) unless the PoP for `msg.key` is confirmed valid — e.g., defer scalar decoding until after `batch.verify_with_vartime_blame()` succeeds, or verify `msg.pop` synchronously before releasing `blame`. Alternatively, have `decrypt` withhold the `EncryptionKeyProof` and produce it only after PoP confirmation. This preserves the invariant documented in `EncryptedMessage` that a blame reveal can never leak a third party's `bX`.

### Proof of Concept
1. Alice (participant 1) runs `generate_secret_shares`, producing `EncryptedMessage { key = X, pop, msg = Enc_bX(share) }` to Bob (participant 2). `X` is transmitted in the clear as the first field of the serialization. [10](#0-9) 
2. Eve (participant 3) builds her share to Bob: set `key = X` (copied), `pop = SchnorrSignature::sign(eve_key, nonce, pop_challenge(context, R, X, THREE, garbage))` — any self-signed PoP under a key she controls will fail verification against `X`, which is fine — and `msg` = random bytes of `SecretShare` length.
3. Bob's `calculate_share` calls `self.encryption.decrypt(...)`, computing `key = ecdh(bob_enc_key, X) = bX` and building `EncryptionKeyProof { key: bX, dleq }`; the PoP failure is only queued. The garbage fails `C::F::from_repr`, so `PedPoPError::InvalidShare { participant: THREE, blame: Some(proof_with_bX) }` is returned and broadcast via `ProcessorMessage::InvalidShare`. [11](#0-10) 
4. Any observer takes `proof.key = bX`, runs `cipher::<C>(context, &bX).apply_keystream(alice_ciphertext)`, and recovers Alice's secret share to Bob — without the DLEq or blame verification ever needing the invalid PoP to pass. Repeating with `t` distinct recipients yields `t` points on Alice's polynomial, recovering her secret coefficient.

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L179-183)
```rust
  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.key.to_bytes().as_ref())?;
    self.pop.write(writer)?;
    self.msg.write(writer)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-500)
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
    )
```

**File:** processor/src/key_gen.rs (L419-425)
```rust
                PedPoPError::InvalidShare { participant, blame } => {
                  Err(ProcessorMessage::InvalidShare {
                    id,
                    accuser: params.i(),
                    faulty: participant,
                    blame: Some(blame.map(|blame| blame.serialize())).flatten(),
                  })?
```
