### Title
Invalid proof-of-possession can still disclose a DKG ECDH key - ([File: crypto/dkg/pedpop/src/lib.rs])

### Summary
`KeyMachine::calculate_share` returns an `EncryptionKeyProof` for a noncanonical secret share before verifying the queued proof-of-possession for the message key. An attacker can therefore reuse an observed encryption key with an invalid PoP and malformed ciphertext, causing the recipient to return the reusable ECDH shared point through the blame path. This re-enables the cross-message disclosure that `EncryptedMessage`'s PoP is explicitly intended to prevent.

### Finding Description
`Encryption::decrypt` queues `msg.pop` for later batch verification, but immediately computes `ecdh(self.enc_key, msg.key)`, decrypts the ciphertext, and constructs an `EncryptionKeyProof` containing that shared point. [1](#0-0) 

The caller then deserializes the decrypted share. If `C::F::from_repr` fails, `calculate_share` returns `PedPoPError::InvalidShare` with `Some(blame)` immediately. [2](#0-1) 

The queued PoP statement is not checked until `batch.verify_with_vartime_blame()`, which is only reached after canonical scalar deserialization and the remaining per-share queueing. [3](#0-2) 

This bypass matters because the code explicitly states that reusing another message's key without a PoP can cause Bob to reveal `bX`, thereby revealing Alice's message encrypted under the same ECDH point. [4](#0-3) 

### Impact Explanation
An attacker who observes `EncryptedMessage.key = X` from Alice to Bob can submit another message to Bob using the same `X`, an invalid PoP, and attacker-selected ciphertext bytes. Bob computes the same ECDH point `bX`; if the decrypted representation is noncanonical, Bob receives a blame proof exposing `bX` even though the attacker's PoP was never verified.

When that blame proof is propagated through the intended blame mechanism, the attacker can extract `bX`, derive the same ChaCha20 keystream, and decrypt Alice's original PedPoP secret-share ciphertext. The proof serialization exposes the shared point directly. [5](#0-4) 

This leaks an honest participant's private DKG share contribution. Possession of enough protected contributions can compromise the victim's resulting threshold secret share and the security of subsequent FROST signing.

### Likelihood Explanation
The attacker needs to observe a target `EncryptedMessage.key`, submit their own malformed message to the victim, and induce the victim to publish or otherwise expose the returned blame proof.

For a 32-byte representation of a roughly 252-bit scalar field, randomly decrypted bytes are noncanonical with probability approximately `7/8`; the attacker can retry until the early error path is reached. The attack does not require knowing the reused key's discrete logarithm, forging a valid PoP, or controlling multiple participants.

### Recommendation
Do not create or return `EncryptionKeyProof` until the message PoP has been verified successfully. Either:

- verify `msg.pop` synchronously inside `Encryption::decrypt` before computing the ECDH point, returning an error that carries no decryption proof; or
- defer ECDH-proof construction until after `batch.verify_with_vartime_blame()` has accepted all queued PoP statements.

The canonical-share check must also not emit a blame proof before the PoP batch succeeds. Ideally, split decryption from blame-proof generation so plaintext can be evaluated without prematurely materializing the reusable ECDH disclosure.

### Proof of Concept
Conceptual attack against participant Bob:

1. Alice sends Bob a legitimate PedPoP share:
   - ephemeral public key `X = xG`;
   - ciphertext `C_A = share_A XOR ChaCha20(KDF(context, bX))`;
   - valid PoP for `X`.
2. Eve observes `X`.
3. Eve submits:
   - `key = X`;
   - `pop =` any invalid `SchnorrSignature`;
   - `msg =` arbitrary ciphertext bytes.
4. Bob's `Encryption::decrypt` queues the invalid PoP, but still computes `S = bX`, decrypts Eve's bytes, and creates `EncryptionKeyProof { key: S, dleq: ... }`. [6](#0-5) 
5. If Eve's decrypted bytes are a noncanonical scalar representation, `calculate_share` returns `InvalidShare { blame: Some(proof) }` before the queued invalid PoP is checked. [7](#0-6) 
6. Bob's blame flow exposes `S`.
7. Eve derives `ChaCha20(KDF(context, S))` and XORs it with Alice's observed ciphertext `C_A`, recovering Alice's encrypted share contribution for Bob.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-90)
```rust
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L267-274)
```rust
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }

  pub fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.key.to_bytes().as_ref())?;
    self.dleq.write(writer)
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L469-499)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L474-482)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L487-499)
```rust
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
