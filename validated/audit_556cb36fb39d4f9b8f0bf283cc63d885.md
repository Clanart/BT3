### Title
Unauthenticated `VerifyBlame` evaluates the share in the wrong direction, letting anyone fatally blame arbitrary DKG participants - ([File: processor/src/key_gen.rs](processor/src/key_gen.rs))

### Summary

The Coolify report is a missing-authorization flaw: knowing only a public identifier (a model UUID) lets a party reach a sensitive path. The analog in Serai is `CoordinatorMessage::VerifyBlame` handling in the DKG key-gen processor: a blame-verification request names only `accuser`/`accused` participant indexes and an attacker-controlled `share`/`blame` payload. There is no check that the accuser actually filed an accusation, and the accusation is evaluated with `sender`/`recipient` swapped, so an unprivileged participant can cause an honest validator to be marked `Blame`d (fatally slashed) or cause a genuine accuser to be blamed instead of a faulty sender. [1](#0-0) 

### Finding Description

`BlameMachine::blame` / `blame_internal` is defined as `blame(sender, recipient, msg, proof)`: it verifies `msg.pop` against `from = sender`, verifies the `EncryptionKeyProof` DLEq against `self.enc_keys[&decryptor]` where `decryptor = recipient`, and returns `sender` if the share is bad or `recipient` if the accusation was false. [2](#0-1) 

An accusation "participant `accused` sent me an invalid share" must therefore be evaluated as `blame(accused, accuser, share_from_accused_to_accuser, proof)`. Instead the processor calls `blame(accuser, accused, ...)` — the roles are reversed:

```rust
let substrate_blame = AdditionalBlameMachine::new(
  context(&id, SUBSTRATE_KEY_CONTEXT), params.n(), substrate_commitment_msgs,
).unwrap().blame(accuser, accused, substrate_share, substrate_blame);
``` [3](#0-2) 

Consequences, by branch:

1. If `share` does not parse as an `EncryptedMessage`, the handler immediately returns `ProcessorMessage::Blame { participant: accused }` — no verification at all ties the accusation to `accuser` or to a real encrypted share. [4](#0-3) 
2. If `share` parses but its PoP does not verify with `from = accuser` (i.e., any real message from a *different* sender), `blame_internal` returns `sender = accuser`, so `Blame { participant: accuser }` is emitted — the named accuser is blamed for a message they never sent. [5](#0-4) 
3. For a *genuine* accusation, the supplied `msg` was authored by `accused`, so `msg.pop.verify` fails under `from = accuser` → `Blame { participant: accuser }`: honest reporters get slashed, faulty senders walk free. [6](#0-5) 

### Impact Explanation

Blame in Serai's DKG leads to fatal slashing of a validator (`ProcessorMessage::Blame`). Any participant able to trigger a `VerifyBlame` message can:

- Name any honest validator as `accused` and submit garbage `share` bytes → validator blamed via the parse-failure early return.
- Name any honest validator as `accuser` and submit any intercepted/other-party encrypted share → PoP fails under `from = accuser` → validator blamed as a false accuser.

Additionally, the swapped argument order means the protocol *always* mis-attributes real faults: an honest participant reporting an invalid share is themselves blamed, while the malicious `accused` is never identified. This is both a griefing/slashing primitive and a complete inversion of the DKG's accountability mechanism.

### Likelihood Explanation

Reachable by any participant in the DKG session using only public participant indexes — the exact analog of "any authenticated user knowing the UUID". The attacker only needs to emit an `InvalidShare` report (or otherwise cause `VerifyBlame` to be dispatched) with attacker-chosen `accuser`, `accused`, `share`, and `blame` fields. No threshold collusion, no compromised coordinator, and no cryptographic break is required; success is deterministic, not probabilistic.

### Recommendation

- Authenticate the accusation: only evaluate `VerifyBlame` for an `accuser` that actually emitted an `InvalidShare` report naming `accused`, and bind the `share`/`blame` bytes to that report.
- Fix the argument order: evaluate `blame(accused, accuser, share_from_accused_to_accuser, proof)` since the accused is the sender and the accuser is the decrypting recipient.
- On unparseable `share`, do not default to blaming `accused`; a malformed payload is the accuser's fault (or evidence-free), not the accused's.

### Proof of Concept

1. Honest validators run PedPoP keygen. Attacker `A` (participant index `a`) receives valid shares, then reports `InvalidShare { accuser: a, faulty: v }` for an arbitrary honest validator `v`.
2. Coordinator dispatches `VerifyBlame { accuser: a, accused: v, share: <bytes>, blame: <proof> }`.
3. To slash `v` directly: `A` sets `share` to any bytes that fail `EncryptedMessage::<Ristretto, SecretShare>::read` → `Blame { participant: v }` is returned at lines 507–513.
4. To slash an arbitrary third party `p`: `A` sets `accuser: p` and supplies any well-formed `EncryptedMessage` whose PoP was not created with `from = p` (e.g., a captured share `q → r`). `blame_internal` fails `msg.pop.verify` with `from = p` and returns `sender = p` → `Blame { participant: p }`. [7](#0-6) 
5. Even without malice, an honest accuser reporting a real bad share from `v` is blamed themselves, because the PoP was created `from = v` but is verified under `from = accuser`.

### Citations

**File:** processor/src/key_gen.rs (L504-563)
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
        let network_blame = AdditionalBlameMachine::new(
          context(&id, NETWORK_KEY_CONTEXT),
          params.n(),
          network_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, network_share, network_blame);

        // If the accused was blamed for either, mark them as at fault
        if (substrate_blame == accused) || (network_blame == accused) {
          return ProcessorMessage::Blame { id, participant: accused };
        }

        ProcessorMessage::Blame { id, participant: accuser }
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-396)
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
```
