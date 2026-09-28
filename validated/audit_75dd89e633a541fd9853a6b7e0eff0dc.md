### Title
Unauthenticated sender can extract a static-ECDH oracle result (recipient's per-message shared key) via blame proof before the proof-of-possession is verified - (File: crypto/dkg/pedpop/src/encryption.rs, crypto/dkg/pedpop/src/lib.rs)

### Summary
GraphQL introspection is an unauthenticated information-disclosure primitive: a public endpoint reveals internal structure the requester was never entitled to. The Serai analog lives in PedPoP's encryption/blame flow: `Encryption::decrypt` unconditionally computes and returns an `EncryptionKeyProof` — which embeds the ECDH shared key `enc_key * msg.key` between the recipient's static decryption key and the sender-supplied `msg.key` — *before* the message's Schnorr proof-of-possession is ever verified (it is only queued into a batch verifier). `KeyMachine::calculate_share` then returns this proof to the caller in `PedPoPError::InvalidShare { blame: Some(..) }` on the early `from_repr` failure path, so an unauthenticated sender holding only public inputs obtains `b·X` for a `C::G` point `X` of their choice — a static-DH oracle and disclosure of material explicitly designed to be revealed only for authenticated messages. [1](#0-0) [2](#0-1) 

### Finding Description
In `Encryption::decrypt` (crypto/dkg/pedpop/src/encryption.rs:469-501) the recipient's secret `enc_key` is combined via `ecdh::<C>(&self.enc_key, msg.key)` and an `EncryptionKeyProof` (shared key + DLEq proof over `[generator, msg.key] → [enc_pub_key, key]`) is constructed unconditionally. The PoP signature is merely `batch_verify`d — queued, not checked (lines 479-485).

In `KeyMachine::calculate_share` (crypto/dkg/pedpop/src/lib.rs:474-499), the loop calls `decrypt`, attempts `C::F::from_repr(share_bytes)`, and on failure returns `PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }` at lines 480-482 — *before* `batch.verify_with_vartime_blame()` at line 493 ever runs. The PoP for that message is never verified on this path.

An attacker therefore sends an `EncryptedMessage` (via `EncryptedMessage::read` at encryption.rs:171) with:
- `key = X`, an arbitrary group element they chose,
- a garbage PoP signature,
- ciphertext that decrypts (under keystream `cipher(context, b·X)`, which they cannot predict — but they don't need to) to non-canonical bytes, which is trivially achieved since decryption happens regardless and random 32-byte strings are non-canonical with high probability (e.g., Ristretto/ed25519 scalar reprs ≥ ℓ).

The recipient errors out with `blame: Some(EncryptionKeyProof)` containing `key = b·X` plus a valid DLEq proof binding it to Bob's registered `enc_pub_key`. The processor serializes and broadcasts this blame (processor/src/key_gen.rs:419-425), making the disclosure public.

### Impact Explanation
The design comments and spec state the shared key is revealed only so a *specific authenticated message* can be publicly decrypted, and the PoP exists precisely because "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X… they'd reveal bX" (encryption.rs:84-91). Here the guard is bypassed by ordering: the reveal happens before authentication completes. Consequences:

- **Static-DH oracle**: an unprivileged party obtains `b·X` for arbitrary `X` with an accompanying DLEq proof certifying it against Bob's public encryption key. This is undisclosed protocol state leakaged to parties who were never entitled to it.
- **Compounding disclosure**: because blame is published, any message Alice sent to Bob reusing the same `msg.key` relation becomes decryptable if key separation is ever violated, and the oracle can be queried repeatedly (one probe per DKG attempt) without ever producing a valid signature.
- Severity aligns with the source class (Medium): real secret-adjacent material disclosed by an unauthenticated party, but bounded — it does not directly yield Bob's `enc_key` or other participants' shares.

### Likelihood Explanation
Reachable by any DKG participant (or anyone able to deliver a `shares` map entry / blame-triggering message to `calculate_share` / `CoordinatorMessage::VerifyBlame`) using only attacker-controlled bytes passed to `EncryptedMessage::read`. No collusion, no valid signature, no honest protocol state required — the early `from_repr` return at lib.rs:480-482 guarantees the code path is hit whenever the garbage ciphertext happens to decode to a non-canonical scalar, which the attacker can force by sending a malformed ciphertext whose plaintext bytes are all `0xff`. Medium likelihood: requires a DKG session position and induces a (detectable) abort, but the disclosure happens before the abort completes.

### Recommendation
Verify the PoP synchronously (or run a per-message `batch.verify` / defer constructing the `EncryptionKeyProof`) before returning any blame material from the deserialization-failure path in `calculate_share`. Concretely: in `Encryption::decrypt`, return `None` for the proof until the queued PoP batch verifies, or in `calculate_share` treat non-canonical shares the same as `BatchId::Decryption` failures (`blame: None`) unless the PoP is known valid — matching the already-correct behavior at lib.rs:495 where a `Decryption` failure yields `blame: None`.

### Proof of Concept
1. Attacker (Participant `E`) constructs `EncryptedMessage::<C, SecretShare<C::F>>` with `key = X` (arbitrary `C::G`), `pop = SchnorrSignature { R: random, s: random }`, and `msg` = 32 bytes of `0xff` (guaranteed non-canonical: `C::F::from_repr` fails).
2. `E` delivers it to honest recipient Bob in the `shares` map argument to `KeyMachine::calculate_share`.
3. Bob's `self.encryption.decrypt(...)` computes `key = b·X` and a valid DLEq proof; `from_repr` fails at lib.rs:480, returning `PedPoPError::InvalidShare { participant: E, blame: Some(proof) }` before `batch.verify_with_vartime_blame()` runs, so the invalid PoP is never detected on this path.
4. Bob's processor emits `ProcessorMessage::InvalidShare { blame: Some(proof.serialize()) }` (processor/src/key_gen.rs:419-425). Attacker recovers `b·X` — a certified static-DH result for a point they selected, obtained without any valid signature — i.e., unauthenticated disclosure of key material the protocol intended to reveal only for authenticated messages.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-500)
```rust
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
