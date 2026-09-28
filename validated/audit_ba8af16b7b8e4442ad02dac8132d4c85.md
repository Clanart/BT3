### Title
Missing own encryption key in `Decryption::enc_keys` causes a panic when processing a blame proof — denial of service ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary

The external report (CVE-2025-24792) describes a crash caused by an unexpected input path reaching a conversion the code did not handle. The analogous pattern in Serai is a lookup the code assumes must succeed but does not: `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]`, yet `enc_keys` is only ever populated with *other* participants' encryption keys. When the local party (the decryptor) is itself the recipient of a blamed message — the normal blame flow — the `HashMap` index panics, crashing the process handling an untrusted `EncryptedMessage`/`EncryptionKeyProof`. [1](#0-0) 

### Finding Description

`Decryption::register` is the only writer to `enc_keys` [2](#0-1) . It is invoked from `Encryption::register` [3](#0-2) , which `SecretShareMachine::verify_r1` calls only for participants whose `EncryptionKeyMessage` was supplied in `commitment_msgs` — i.e., everyone *except* `params.i()`, since `validate_map` requires `map.len() + 1 == included.len()` and treats our own index as implicit [4](#0-3) . Our own `Participant` is therefore never inserted into `enc_keys`.

When a counterparty sends a malformed `EncryptedMessage` (bad share value — exactly what `calculate_share` reports as `InvalidShare { participant: l, blame: Some(proof) }` [5](#0-4) ), the honest party resolves fault via `BlameMachine::blame` / `AdditionalBlameMachine::blame` → `blame_internal(sender, recipient = our_i, msg, Some(proof))` [6](#0-5) . Inside `decrypt_with_proof`, the DLEq verification requires the *decryptor's* encryption public key and evaluates `self.enc_keys[&decryptor]` [7](#0-6) . Because `decryptor` is our own `Participant`, which was never registered, indexing the `HashMap` panics (`HashMap`'s `Index` impl panics on absent keys) instead of returning `DecryptionError`.

Two adjacent panic sites of the same shape exist: `self.commitments[&sender]` in `blame_internal` panics if blame is evaluated for a `sender` not among the DKG participants [8](#0-7) , and `Encryption::encrypt` panics on `self.decryption.enc_keys[&participant]` for any unregistered participant [9](#0-8) .

### Impact Explanation

An unprivileged DKG participant can force every honest party to crash during fault resolution by sending a syntactically valid `EncryptedMessage<C, SecretShare<C::F>>` whose decrypted share is a valid scalar failing verification (or a non-canonical serialization — the `from_repr` failure path at `crypto/dkg/pedpop/src/lib.rs:480`). The intended outcome is a `PedPoPError::InvalidShare` with a blame proof; instead the honest node panics inside `decrypt_with_proof`. This converts a misbehavior that should be attributable (blame → exclusion → retry) into a hard crash of the affected process, which for Serai's deployment aborts key generation / signing infrastructure — a direct availability analog of the reported signed-to-unsigned crash. It also silently defeats the blame mechanism: the faulty party cannot be identified because the honest node dies before returning a `Participant`.

### Likelihood Explanation

Reachability requires only that a DKG participant emit one invalid encrypted share — fully within an unprivileged signer's capability, since `EncryptedMessage` bytes are attacker-controlled and read via `EncryptedMessage::read` [10](#0-9) . No collusion, no special position: any single faulty participant triggers it deterministically whenever the victim requests proof-bearing blame, which is the documented path for attributing `InvalidShare` faults. The panic is unconditional on that path because `enc_keys` provably never contains the local participant.

### Recommendation

Replace the infallible `HashMap` index with a lookup returning `DecryptionError::InvalidProof` on absence (`self.enc_keys.get(&decryptor).ok_or(...)`), and register the local participant's own encryption public key into `Decryption::enc_keys` during `Encryption::new` so the DLEq against `enc_keys[&decryptor]` has the intended base point. Apply the same treatment to `self.commitments[&sender]` in `blame_internal` (return `sender` as faulty or an error on absence) and to `self.decryption.enc_keys[&participant]` in `Encryption::encrypt`.

### Proof of Concept

```rust
// Conceptual; uses the pedpop test harness shape (Ristretto, 2-of-2).
// 1. Both parties run generate_coefficients and exchange EncryptionKeyMessage<Commitments>.
// 2. Party 2 encrypts a SecretShare whose value fails verification
//    (e.g., via the same technique as invalidate_share_value).
// 3. Party 1 runs calculate_share -> Err(InvalidShare { participant: p2, blame: Some(proof) }).
// 4. Party 1 calls blame_machine.blame(p2, p1, msg, Some(proof)).
//
// Expected: (AdditionalBlameMachine, p2)
// Actual:   panic at crypto/dkg/pedpop/src/encryption.rs:388 —
//           `self.enc_keys[&decryptor]` where decryptor == p1, and p1 was
//           never registered in enc_keys (only counterparties are, via
//           Encryption::register in SecretShareMachine::verify_r1).
```

Caveat: I verified the registration asymmetry and the panic site statically; the panic additionally assumes the blame path is invoked with `proof: Some(..)`, which is the intended usage for invalid-share blame (the `None` variant is only for invalid-signature accusations, which error earlier at the `pop.verify` check).

### Citations

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L452-458)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L62-80)
```rust
  if (map.len() + 1) != included.len() {
    Err(PedPoPError::IncorrectAmountOfParticipants {
      expected: included.len(),
      found: map.len() + 1,
    })?;
  }

  for included in included {
    if *included == ours {
      if map.contains_key(included) {
        Err(PedPoPError::DkgError(DkgError::DuplicatedParticipant(*included)))?;
      }
      continue;
    }

    if !map.contains_key(included) {
      Err(PedPoPError::MissingParticipant(*included))?;
    }
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
