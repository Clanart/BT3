The SSRF analog here would be a path where attacker-controlled input causes a victim's secret material to be revealed. The strongest candidate is PedPoP's blame mechanism, which publishes the ECDH shared key derived from the victim's static `enc_key` and an attacker-controlled `msg.key`. Let me verify how the PoP is enforced and whether reuse/torsion bypasses exist.### Title
PedPoP blame proof leaks the ECDH key for an unrelated honest message because the per-message proof-of-possession is never verified before the secret share deserialization error returns blame - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The Impala advisory's class — an authenticated participant invoking a function that reaches into protected secret material and exfiltrates it — maps onto PedPoP's blame flow. `Encryption::decrypt` computes the ECDH shared key `enc_key * msg.key` and wraps it into an `EncryptionKeyProof` **unconditionally**, while the Schnorr proof-of-possession for `msg.key` is only *queued* into a `BatchVerifier`. In `KeyMachine::calculate_share`, a non-canonical decrypted share triggers an early `Err` return that embeds `blame: Some(proof)` — before `batch.verify_with_vartime_blame()` is ever executed. A malicious sender can therefore set `msg.key` to a key they do not own (specifically, the per-message public key from an honest participant's encrypted share to the same victim), have their forged PoP go unchecked, and coerce the victim into publishing the ECDH key that decrypts the honest ciphertext.

### Finding Description
The code comments in `EncryptedMessage` explicitly acknowledge this exact attack: if Eve reuses Alice's per-message key `X`, Bob's blame would reveal `bX`, revealing Alice's message to Bob — which is why the `pop` field exists [1](#0-0) . The spec reiterates that the PoP is what prevents key co-option [2](#0-1) .

However, the mitigation is only enforced inside the batch verifier. In `Encryption::decrypt`, `msg.pop.batch_verify(...)` merely queues the PoP, and `key = ecdh(&self.enc_key, msg.key)` plus the `EncryptionKeyProof` (containing `key` in plaintext) are computed and returned regardless of whether the PoP is valid [3](#0-2) .

In `KeyMachine::calculate_share`, the loop calls `self.encryption.decrypt(...)`, then immediately checks `C::F::from_repr(share_bytes.0)`. On failure it returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }` — an early return that skips `batch.verify_with_vartime_blame()` at line 493 entirely [4](#0-3) . ChaCha20-decrypted garbage under the "wrong" key yields non-canonical 32-byte scalar encodings with overwhelming probability (~15/16 for a ~2^252-order field), so the attacker can reliably force this path.

`msg.key` is read straight from the wire via `C::read_G` in `EncryptedMessage::read`, so it is fully attacker-controlled [5](#0-4) . The published `proof.key` is accompanied by a valid DLEq proving `proof.key = enc_key_victim * msg.key` relative to `enc_keys[decryptor]` [6](#0-5) , so any observer — including the coordinator's `VerifyBlame` path, which processes `blame` via `EncryptionKeyProof::read` [7](#0-6)  — can verify authenticity and use `cipher(context, proof.key)` to decrypt Alice's original ciphertext.

### Impact Explanation
`cipher(context, proof.key)` reconstructs the exact ChaCha20 keystream used for Alice's encrypted share to the victim, exposing Alice's secret-share contribution `s_alice(victim)`. While a single summand does not by itself recover the victim's full secret share (which is a sum over all senders), it is an unauthorized disclosure of protocol secret material that the PoP mechanism was specifically introduced to prevent, and each malicious sender in a DKG session can leak one honest sender→victim share. Over repeated DKG attempts an attacker can repeatedly pick new victims/targets. Because shares and blame proofs propagate to the coordinator and other participants for fault resolution, the leaked key — and thus the plaintext share — is effectively broadcast.

### Likelihood Explanation
The attack requires only that the malicious participant observe an honest `EncryptedMessage` addressed to the victim (these messages transit authenticated but not confidential channels and are routinely relayed), copy its `key` field into their own message, attach any bytes as `pop` and `msg`, and ensure the plaintext under the resulting key is non-canonical — a ~94% chance per random ciphertext, retryable trivially by tweaking the ciphertext. No cryptography is broken and no cooperation is needed. Blame attribution still correctly faults Eve (the forged PoP fails `decrypt_with_proof` → `InvalidSignature` → sender blamed [8](#0-7) ), but by then the ECDH key has already been emitted.

### Recommendation
Do not produce or return the `EncryptionKeyProof` before the PoP is known to be valid. Concretely, in `Encryption::decrypt`, either (a) verify `msg.pop` synchronously before computing `key`/`EncryptionKeyProof` and return a blame-less error on failure, or (b) defer attaching `blame` in `calculate_share` until after `batch.verify_with_vartime_blame()` confirms the `BatchId::Decryption(l)` statement for that sender — matching the existing convention where `BatchId::Decryption` failure yields `blame: None`. Additionally, `C::F::from_repr` failure should be treated identically to a share-verification failure and only emit a proof whose PoP has passed.

### Proof of Concept
Setup: honest participants Alice (sender) and Bob (victim, `enc_key_b`), and malicious Eve, in one PedPoP session with context `ctx`.

1. Bob registers all participants; observe Alice's wire message `EncMsg_A→B` and extract `X_A = EncMsg_A→B.key`.
2. Eve constructs `EncMsg_E = EncryptedMessage { key: X_A, pop: <arbitrary bytes parsed as SchnorrSignature>, msg: <random 32-byte ciphertext> }` and delivers it to Bob under participant id `E`.
3. Bob calls `KeyMachine::calculate_share(rng, shares)` where `shares[E] = EncMsg_E`.
4. Inside `Encryption::decrypt`: the PoP on `X_A` is queued (never verified), `key = enc_key_b * X_A` is computed, the ChaCha20 keystream is applied to `msg` yielding garbage, and `(garbage, EncryptionKeyProof { key, dleq })` is returned.
5. `C::F::from_repr(garbage)` fails (prob ≈ 1 − l/2^256), so `calculate_share` returns `Err(PedPoPError::InvalidShare { participant: E, blame: Some(proof) })` with `proof.key = enc_key_b * X_A` — before `batch.verify_with_vartime_blame()` is reached.
6. Bob publishes `blame` (e.g., `ProcessorMessage::InvalidShare { ..., blame }` → `VerifyBlame`). Any observer computes `cipher(ctx, proof.key)` and applies it to `EncMsg_A→B.msg`, recovering Alice's secret share `s_alice(Bob)` in the clear — exfiltrated secret material whose container (Alice's ciphertext) was publicly known, directly analogizing the credential-provider exfiltration in the Impala report.

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-393)
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

**File:** spec/cryptography/Distributed Key Generation.md (L28-35)
```markdown
While key reuse by a participant is considered as them revealing the messages
themselves, and therefore out of scope, there is an attack where a malicious
adversary claims another participant's encryption key. They'll fail to encrypt
their message, and the recipient will issue a blame statement. This blame
statement, intended to reveal the malicious adversary, also reveals the message
by the participant whose keys were co-opted. To resolve this, a
proof-of-possession is also included with encrypted messages, ensuring only
those actually with per-message keys can claim to use them.
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

**File:** crypto/dkg/pedpop/src/lib.rs (L582-588)
```rust
    let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) {
      Ok(share_bytes) => share_bytes,
      // If there's an invalid signature, the sender did not send a properly formed message
      Err(DecryptionError::InvalidSignature) => return sender,
      // Decryption will fail if the provided ECDH key wasn't correct for the given message
      Err(DecryptionError::InvalidProof) => return recipient,
    };
```

**File:** processor/src/key_gen.rs (L538-549)
```rust
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
