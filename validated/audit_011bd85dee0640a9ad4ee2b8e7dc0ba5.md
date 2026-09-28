### Title
Unauthenticated blame framing — an absent `EncryptionKeyProof` causes `BlameMachine` to fault the honest recipient - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The CubeCart flaw is a logic error where an attacker-controlled parameter (`force_unsubscribe=1`) lets an unauthenticated party force-removal of a victim's data. The PedPoP analog lives in the blame-disclosure path: `Decryption::decrypt_with_proof` treats a *missing* `proof` (`Option::None`) the same as a *failing* proof and returns `DecryptionError::InvalidProof`, which `BlameMachine::blame_internal` maps to "the recipient is faulty". Any party holding a perfectly valid, honestly-constructed `EncryptedMessage` from sender `S` to recipient `R` can therefore obtain a verdict that `R` is faulty simply by calling `blame(S, R, msg, None)`, without knowing the decryption key or producing any proof.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs`, `decrypt_with_proof` verifies the per-message Schnorr PoP and then branches on `proof`: [1](#0-0) 

When `proof` is `None`, it unconditionally returns `Err(DecryptionError::InvalidProof)`. `BlameMachine::blame_internal` in `crypto/dkg/pedpop/src/lib.rs` maps that error to the recipient: [2](#0-1) 

The intended semantics of `Option<EncryptionKeyProof>` are "no proof exists only when the accusation is an invalid PoP/signature" — but that intent is never enforced. If the PoP verifies, a `None` proof still yields `InvalidProof` → recipient blamed. Neither `BlameMachine::blame` nor `AdditionalBlameMachine::blame` distinguishes "accusation of invalid signature" from "accusation of invalid share", so a caller-controlled `None` acts exactly like CubeCart's `force_unsubscribe=1`: a single attacker-chosen input selects the victim to be marked faulty.

### Impact Explanation
A blame verdict is consumed by consensus-adjacent code that fatally slashes/removes the identified participant (`processor/src/key_gen.rs` `VerifyBlame` feeds `AdditionalBlameMachine::new(...).blame(accuser, accused, share, blame)` and the result drives `fatal_slash` in the tributary handler). Encrypted DKG shares are published on the public tributary (`Transaction::DkgShares`), so the `msg` bytes are available to anyone. An accuser can take the genuine, valid share message addressed to an honest participant, submit it with no `EncryptionKeyProof`, and have every honest evaluator conclude the *recipient* is faulty — removing an honest validator from the set without their consent or any fault on their part, mirroring the unauthorized-forced-removal class of the CVE.

### Likelihood Explanation
Reachable by any party able to lodge a blame accusation with public transcript data: the message authenticates fine (PoP verifies against the real sender), so no cryptographic break is needed — only omitting the optional proof. The only mitigation is that blame accusations originate from DKG participants, so this requires an accusing participant or a path accepting unauthenticated blame data; within the library itself there is no check preventing the framing.

### Recommendation
Make the `Option<EncryptionKeyProof>` semantics explicit: in `decrypt_with_proof`/`blame_internal`, if `proof` is `None`, only blame the sender when the PoP/signature actually failed; if the PoP verifies and no proof is supplied, return a distinct error (e.g., `MissingProof`) that `blame_internal` maps to blaming the accuser/recipient only when justified, or reject the blame as malformed rather than assigning fault.

### Proof of Concept
1. Run an honest PedPoP round; obtain the public `EncryptedMessage<C, SecretShare>` from honest sender `S` to honest recipient `R` (published on the tributary in `DkgShares`).
2. Construct `commitment_msgs` honestly and build `AdditionalBlameMachine::new(context, n, commitment_msgs)`.
3. Call `.blame(S, R, msg, None)` — i.e., accuse `R` using the valid message but supply no `EncryptionKeyProof`.
4. Result: PoP verifies → `proof` is `None` → `DecryptionError::InvalidProof` → `blame_internal` returns `R` as the faulty party, framing an honest participant for removal, exactly as `force_unsubscribe=1` removed unconsenting subscribers.

### Citations

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
