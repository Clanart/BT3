### Title
VerifyBlame passes accuser/accused in reversed order, so an accusation never authenticates the accused as the message's origin and can wrongfully slash an honest validator - (File: processor/src/key_gen.rs)

### Summary
GHSA-hqwm-7x7x-8379 concerns a server accepting cross-origin input because the origin (`CheckOrigin`) is not validated. The analog in Serai is `CoordinatorMessage::VerifyBlame` handling in `processor/src/key_gen.rs`: the PedPoP blame evaluation is invoked with `sender`/`recipient` swapped, so the per-message proof-of-possession — the mechanism that binds an `EncryptedMessage` to its actual origin — is checked against the accuser's identity instead of the accused's. A blame claim is therefore adjudicated without ever validating that the share actually originated from the accused.

### Finding Description
`BlameMachine::blame` / `AdditionalBlameMachine::blame` take `(sender, recipient, msg, proof)` where `sender` is the accused dealer and `recipient` is the accuser, per the documented contract ("the encrypted secret share from the accused sender to the accusing recipient") [1](#0-0) . Inside `blame_internal`, `decrypt_with_proof(sender, recipient, ...)` verifies `msg.pop` against `pop_challenge(..., from = sender, ...)` — i.e., the PoP authenticates the claimed origin of the ciphertext [2](#0-1) . The tests confirm the ordering: `machine.blame(ONE, TWO, ...)` where `ONE` is the malicious sender and `TWO` the accuser [3](#0-2) .

In `processor/src/key_gen.rs`, however, both substrate and network blame checks are called as `.blame(accuser, accused, share, blame)` — sender and recipient reversed [4](#0-3) . Two consequences:

1. A legitimate accusation fails: a real invalid share sent by `accused` has its PoP bound to `from = accused`, but it is verified against `from = accuser`, so `pop.verify` fails and `blame_internal` returns `sender` = `accuser`, slashing the honest accuser and letting the malicious dealer escape.
2. A malicious accuser can frame an honest validator: they craft a fresh `EncryptedMessage` whose PoP is bound to `from = accuser` (they can sign for themselves), compute `msg.key = k·G` and `proof.key = k·enc_pub[accused]` (public), produce a valid DLEq proof, and encrypt a share that is canonical and valid under `commitments[accuser]`... wait — under the swapped call, `commitments[&sender]` is `commitments[accuser]`, and the accuser cannot produce a valid share for the accused's polynomial. However `blame_internal` returns `recipient` (= `accused`) on `DecryptionError::InvalidProof` [5](#0-4) : the accuser supplies `proof = None` or an invalid `EncryptionKeyProof`, causing `InvalidProof` to be returned and `accused` to be blamed — with no requirement that the accused ever sent anything.

Note the asymmetry: the valid-PoP path with a valid decryption proof requires a valid share under `commitments[accuser]` (hard for the accuser), but the `InvalidProof` path requires only that the PoP verifies — which the accuser fully controls by signing `pop` for `from = accuser`, exactly what the swapped call checks.

### Impact Explanation
`VerifyBlame` results in `ProcessorMessage::Blame { participant }`, which triggers a fatal slash of the named validator. An attacker can therefore cause an honest validator to be blamed/slashed by submitting a self-constructed `EncryptedMessage` plus a garbage or absent `EncryptionKeyProof`, since the origin check (PoP bound to `sender`) is evaluated against the accuser, not the accused. Conversely, a genuinely malicious dealer who sends an invalid share is never successfully blamed because their PoP is bound to `from = accused` and the check runs against `from = accuser`, causing `InvalidSignature` to blame the honest accuser. Both directions break the accountability mechanism the DKG relies on.

### Likelihood Explanation
Any validator participating in a key-gen can issue a `VerifyBlame` accusation (or be the target of one). The attack requires no collusion: the accuser crafts the share bytes locally (the `pop` Schnorr signature over `pop_challenge(context, R, key, accuser, msg)` is trivially producible) and submits them through the normal blame path. Likewise, every honest accusation of a real invalid share deterministically backfires onto the accuser, so the bug triggers in the ordinary fault-handling flow, not just under active attack.

### Recommendation
Swap the arguments in both calls so `sender = accused` and `recipient = accuser`: `.blame(accused, accuser, substrate_share, substrate_blame)` and `.blame(accused, accuser, network_share, network_blame)` in `processor/src/key_gen.rs` (lines ~543–556). Additionally, consider requiring `blame`/`proof` to be `Some` before attributing `InvalidProof` to the accused — a missing proof should implicate the accuser's submission, not the accused — and add a regression test where an accusation with a fabricated share cannot blame an honest party.

### Proof of Concept
1. Complete a DKG among `n` participants, producing `CommitmentsDb` entries and honest `accused` never sending a bad share.
2. Accuser builds `EncryptedMessage` locally: pick `k`, set `key = k·G`, pick arbitrary ciphertext bytes `msg`, and set `pop = SchnorrSignature::sign(k, nonce, pop_challenge(context, nonce·G, key, accuser, msg))` — valid because the swapped call verifies `from = accuser`.
3. Submit `CoordinatorMessage::VerifyBlame { id, accuser, accused, share: <crafted bytes concatenated twice for substrate+network layouts>, blame: None }`.
4. `blame_internal` → `decrypt_with_proof(accuser, accused, msg, None)` → PoP verifies → `proof` is `None` → `Err(InvalidProof)` → returns `recipient = accused`.
5. `substrate_blame == accused` → `ProcessorMessage::Blame { id, participant: accused }`: the honest validator is fatally blamed for a share that provably never originated from them — the exact "unvalidated origin" failure class of the DevSpace advisory, expressed in Serai's blame-attribution path.

### Citations

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

**File:** crypto/dkg/pedpop/src/lib.rs (L615-629)
```rust
  /// The message should be a copy of the encrypted secret share from the accused sender to the
  /// accusing recipient. This message must have been authenticated as actually having come from
  /// the sender in question.
  ///
  /// In order to enable detecting multiple faults, an `AdditionalBlameMachine` is returned, which
  /// can be used to determine further blame. These machines will process the same blame statements
  /// multiple times, always identifying blame. It is the caller's job to ensure they're unique in
  /// order to prevent multiple instances of blame over a single incident.
  pub fn blame(
    self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> (AdditionalBlameMachine<C>, Participant) {
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

**File:** crypto/dkg/pedpop/src/tests.rs (L156-159)
```rust
    let (additional, blamed) = machine.blame(ONE, TWO, msg.clone(), blame.clone());
    assert_eq!(blamed, ONE);
    // Verify additional blame also works
    assert_eq!(additional.blame(ONE, TWO, msg.clone(), blame.clone()), ONE);
```

**File:** processor/src/key_gen.rs (L543-560)
```rust
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
```
