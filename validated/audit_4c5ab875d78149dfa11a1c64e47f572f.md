### Title
Forged blame evidence lets anyone falsely convict an innocent DKG participant - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP blame protocol evaluates an accusation using only attacker-supplied artifacts — an `EncryptedMessage` and an optional `EncryptionKeyProof` — without binding the message to the accused sender. Any observer can fabricate a "share message" attributed to an honest participant and obtain a verdict marking that participant as faulty. This mirrors CVE-2020-13359's shape: an operation (blame adjudication) accepts a self-generated credential (a self-signed PoP / a self-computable ECDH proof) as if it were evidence produced by the accused, letting a lower-privilege party overwrite accountability state while bypassing the protocol's integrity controls.

### Finding Description
`BlameMachine::blame` / `AdditionalBlameMachine::blame` delegate to `blame_internal`, which calls `Decryption::decrypt_with_proof` on the caller-supplied `msg`. The PoP check verifies a Schnorr signature under `msg.key` — a public key that is *part of the message itself*: [1](#0-0) 

`pop_challenge` binds the claimed `from` index into the challenge, but the signing key `msg.key` is a fresh ephemeral scalar chosen by whoever created the ciphertext — it is never authenticated against the accused participant. Two trivially reachable forgery paths follow:

1. **Invalid-PoP path.** `blame_internal` returns `sender` on `DecryptionError::InvalidSignature` — meaning an accuser can submit arbitrary garbage bytes as `msg`; the PoP fails, and the honest sender is declared faulty. No proof or key knowledge is needed. [2](#0-1) 

2. **Fabricated-content path.** `AdditionalBlameMachine::new` is explicitly usable by non-members and only needs the public commitment messages: [3](#0-2) 
An accusing *participant* (or anyone who knows the victim-recipient relationship where they control the `enc_key`) can pick `msg.key = g·k′`, compute the ECDH key `enc_priv·msg.key` themselves, produce a valid `EncryptionKeyProof` (the DLEq only proves consistency between `enc_keys[decryptor]`, `msg.key`, and `proof.key` — all satisfiable by the forger), and encrypt an invalid scalar. `decrypt_with_proof` accepts it, `from_repr`/share-verification fails, and `blame_internal` returns `sender`: [4](#0-3) [5](#0-4) 

Nothing in `blame`/`blame_internal`/`decrypt_with_proof` ties `msg` to a message the accused actually sent. The only safeguard is the doc comment "This message must have been authenticated as actually having come from the sender" — yet the in-tree consumer passes the raw `share` blob from `VerifyBlame` calldata straight into `AdditionalBlameMachine::blame` with no sender-authentication check at all: [6](#0-5) [7](#0-6) 

When the forged evidence yields `substrate_blame == accused` or `network_blame == accused`, the accused — an honest participant — is returned as `ProcessorMessage::Blame` and treated as faulty.

### Impact Explanation
Every innocent participant in a PedPoP key generation can be unilaterally declared faulty by any other participant (invalid-PoP path requires zero secrets) or by any party able to submit blame evidence (fabricated-content path). Where blame results in slashing or removal from the validator set, this is a direct, unprivileged attack on honest operators: an attacker frames victims, aborts the DKG, and gets the victim punished. It is the audit-control bypass analog of the GitLab bug: the adjudication mechanism that is supposed to hold cheaters accountable instead hands a forgery primitive to the accuser.

### Likelihood Explanation
Triggering requires only invoking the public `blame` API (or the `VerifyBlame` coordinator message) with fabricated bytes — no threshold collusion, no secret knowledge for the invalid-PoP variant, and for the valid-looking variant only the accuser's own encryption key, which every legitimate participant possesses. The needed inputs (commitment messages) are protocol-public. The only barrier is the documentation precondition, which the in-repo consumer demonstrably does not enforce.

### Recommendation
Authenticate the accused's message inside the blame evaluation itself rather than deferring to the caller. Concretely: during `calculate_share`/key-gen, retain (or commit to) a per-sender transcript binding — e.g., require each `EncryptedMessage`/`EncryptionKeyMessage` to carry a signature under the sender's registered identity/encryption key over the serialized share message, and have `blame_internal` verify that signature against the sender's registered `enc_key`/identity before considering any blame evidence. Alternatively, hash commitments to each participant's share messages in the commitment round and require `blame` to reject messages not matching the committed hash. At minimum, reject accusations whose `msg` fails PoP verification under a key provably registered to the sender, and do not treat `InvalidSignature` as sender fault when the message's provenance is unverified.

### Proof of Concept
For the zero-secret variant:
1. An accuser constructs `msg` as arbitrary bytes deserialized into `EncryptedMessage` (any `key`, any `pop`, any `msg`).
2. Calls `AdditionalBlameMachine::new(context, n, commitment_msgs)` with the public commitment messages, then `blame(honest_sender, accuser, forged_msg, None)`.
3. `msg.pop.verify` fails → `DecryptionError::InvalidSignature` → `blame_internal` returns `honest_sender`. The innocent sender is declared faulty.

For the self-decrypting variant, a malicious participant who is a legitimate recipient (`decryptor`): generate `k′`, set `msg.key = g·k′`, self-sign a valid `pop` under `k′` (they know the discrete log), set `msg.msg` to a ciphertext of `0xFF…` under `cipher(context, enc_priv·msg.key)`, and produce a valid `EncryptionKeyProof` via `DLEqProof::prove` with their own `enc_key`. `decrypt_with_proof` verifies the DLEq against `enc_keys[decryptor]` and `msg.key` (both consistent by construction), decryption yields a non-canonical scalar, and `blame_internal` again returns `sender`.

One caveat to flag honestly: the library documents that the message "must have been authenticated as actually having come from the sender," so severity depends on the consuming protocol failing to authenticate — which is the case for the `VerifyBlame` flow shown in `processor/src/key_gen.rs`, where the `share` field is unauthenticated calldata (note that file is outside the listed production scope but demonstrates the reachable deployment of the in-scope `pedpop` APIs).

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L374-379)
```rust
    if !msg.pop.verify(
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    ) {
      Err(DecryptionError::InvalidSignature)?;
    }
```

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

**File:** crypto/dkg/pedpop/src/lib.rs (L590-605)
```rust
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L639-662)
```rust
  /// Create an AdditionalBlameMachine capable of evaluating Blame regardless of if the caller was
  /// a member in the DKG protocol.
  ///
  /// Takes in the parameters for the DKG protocol and all of the participant's commitment
  /// messages.
  ///
  /// This constructor assumes the full validity of the commitment messages. They must be fully
  /// authenticated as having come from the supposed party and verified as valid. Usage of invalid
  /// commitments is considered undefined behavior, and may cause everything from inaccurate blame
  /// to panics.
  pub fn new(
    context: [u8; 32],
    n: u16,
    mut commitment_msgs: HashMap<Participant, EncryptionKeyMessage<C, Commitments<C>>>,
  ) -> Result<Self, PedPoPError<C>> {
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
  }
```

**File:** processor/src/key_gen.rs (L504-522)
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
```

**File:** processor/src/key_gen.rs (L543-561)
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
        }
```
