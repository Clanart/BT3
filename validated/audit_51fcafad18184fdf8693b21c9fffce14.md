### Title
Premature blame proof leaks the ECDH shared key before the per-message PoP is verified, re-enabling the key-reuse attack the PoP was designed to prevent - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP `EncryptedMessage` format includes a Schnorr proof-of-possession (`pop`) over the ephemeral encryption key specifically so that a participant who copied someone else's ephemeral key cannot trigger a blame statement that reveals the ECDH shared key of the victim's real message. However, `KeyMachine::calculate_share` returns `PedPoPError::InvalidShare { blame: Some(blame) }` — containing the full `EncryptionKeyProof` with the decrypted ECDH point — on the "share isn't a canonical scalar" path *before* the batched PoP verification ever runs. A malicious participant can therefore harvest the shared key for a message they never legitimately encrypted, defeating the protection described in the code comments and the DKG spec.

### Finding Description
`EncryptedMessage` documents the exact attack: "If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X... they'd reveal bX, revealing Alice's message to Bob" [1](#0-0) . The spec repeats this rationale [2](#0-1) .

In `Encryption::decrypt`, the PoP is only *queued* into a `BatchVerifier` (deferred verification), while the ECDH key is computed, the keystream applied, and an `EncryptionKeyProof` revealing the shared key is returned unconditionally [3](#0-2) .

In `calculate_share`, the decrypted bytes are parsed via `C::F::from_repr`, and on failure the function returns `InvalidShare` with `blame: Some(blame.clone())` immediately — before `batch.verify_with_vartime_blame()` at the end of the loop ever validates the queued PoP [4](#0-3) . The `BatchId::Decryption(l) => blame: None` mapping exists to suppress blame when the PoP fails, but the early `from_repr` return bypasses it entirely [5](#0-4) .

### Impact Explanation
Eve observes Alice's `EncryptedMessage` to Bob carrying ephemeral key `X = k·G`. Eve sends Bob a forged `EncryptedMessage` reusing `key = X`, an invalid `pop` (Eve doesn't know `k`), and ciphertext of all-`0xFF` bytes (guaranteed non-canonical scalar). Bob's `decrypt` computes `ecdh = b·X` (Bob's enc key × X), producing garbage plaintext; `from_repr` fails; `calculate_share` returns `blame: Some(EncryptionKeyProof { key: b·X, dleq })`. Bob's processor forwards this blame to the coordinator (`processor/src/key_gen.rs` `InvalidShare { blame }`), publicly revealing `b·X` — which is exactly the ChaCha20 shared-key input for Alice's genuine message to Bob. Anyone can now recompute `cipher(context, b·X)` and recover Alice's secret share to Bob, leaking a DKG secret share (key share recovery) that the PoP was introduced specifically to protect. The PoP is never verified on this path, so it provides no protection.

### Likelihood Explanation
The attacker only needs to be a DKG participant able to observe an honest `EncryptedMessage` (broadcast/shared-medium visibility is the documented threat model) and send one malformed message to its recipient. The non-canonical-scalar trigger is deterministic (all-`0xFF` bytes always fail `from_repr`), and the blame mechanism actively propagates the leaked key to all participants — the worst-case amplification.

### Recommendation
Do not emit the `EncryptionKeyProof` blame until the PoP has been verified. Concretely, in `calculate_share`, verify the queued PoP statements (or perform PoP verification synchronously inside `decrypt`) before any error path can return `blame: Some(...)`; alternatively, gate the `InvalidShare` early-return on a prior PoP check so non-canonical plaintext from an unproven key yields `blame: None`.

### Proof of Concept
```rust
// In a PedPoP session, Eve (participant l) targets Bob (participant i).
// 1. Observe Alice -> Bob EncryptedMessage; copy its `key` point X.
let mut forged: EncryptedMessage<C, SecretShare<C::F>> = alice_to_bob_msg.clone();
// 2. Replace the ciphertext with bytes guaranteed to fail C::F::from_repr
forged.msg.as_mut().as_mut().fill(0xFF);
// 3. `pop` may be any parseable (R, s); it is never checked before blame is emitted
//    (pop is only queued into `batch` inside Encryption::decrypt).
// 4. Bob runs:
let err = bob_machine.calculate_share(rng, shares_including_forged).unwrap_err();
// err == PedPoPError::InvalidShare { participant: eve, blame: Some(proof) }
// proof.key == ecdh(bob_enc_key, X) — the shared key of Alice's real message.
// Anyone receiving this blame recomputes cipher(context, proof.key)
// and decrypts Alice's genuine share to Bob.
```

Root cause: `self.encryption.decrypt` defers PoP verification into `batch` yet returns the key-revealing `EncryptionKeyProof` eagerly [6](#0-5) , and `calculate_share` propagates it via `blame: Some(blame.clone())` before `batch.verify_with_vartime_blame()` runs [7](#0-6) .

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
