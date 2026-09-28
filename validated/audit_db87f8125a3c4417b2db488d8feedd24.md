### Title
Missing validation hook on blame evaluation inputs allows participant-index panic and blame on unverified commitments - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary

The X.Org bug is a resource created without passing through the authorization/labeling hook (XACE), so a later access dereferences a NULL SID and crashes the server. The Serai analog is the PedPoP blame path: `AdditionalBlameMachine::new` registers per-participant commitment/encryption-key material directly into `BlameMachine.commitments` and `Decryption.enc_keys` without performing the round-1 validation hook (`verify_r1`, which checks the Schnorr PoK and `commitments.len() == t`), and the subsequent `blame`/`decrypt_with_proof` evaluation indexes `enc_keys` and `commitments` by attacker-supplied `Participant` values that were never checked against `params.n()`. A blame object carrying an out-of-range accuser/accused index reaches a `HashMap` indexing panic — the same "unlabeled object used later" crash shape.

### Finding Description

`SecretShareMachine::verify_r1` is the labeling hook of the PedPoP protocol: it authenticates each `EncryptionKeyMessage<Commitments>` — checking `msg.commitments.len() == params.t()` (crypto/dkg/pedpop/src/lib.rs:317) and batch-verifying the proof-of-knowledge signature bound to context and participant (lines 323-334). Only material that passes this hook is stored into `commitments`/`encryption.decryption.enc_keys`.

`AdditionalBlameMachine::new` bypasses that hook entirely. It calls `encryption.register(i, msg)` for every `i in 1..=n`, which inserts `msg.enc_key` into `enc_keys` and `msg.commitments` into the blame machine's commitment map with no PoK verification and no length check (crypto/dkg/pedpop/src/lib.rs:654-661). The docstring admits this: "Usage of invalid commitments is considered undefined behavior, and may cause everything from inaccurate blame to panics" (lines 645-648).

Then `blame`/`blame_internal` evaluates the attacker-supplied `EncryptedMessage` and optional `EncryptionKeyProof` via `Decryption::decrypt_with_proof`, which indexes `self.enc_keys[&decryptor]` (crypto/dkg/pedpop/src/encryption.rs:388) and `share_verification_statements` indexes `self.commitments[&l]` (crypto/dkg/pedpop/src/lib.rs:490). Neither `sender` (accused) nor `recipient`/`decryptor` (accuser) is validated to be `<= params.n()`.

The reachable path is `CoordinatorMessage::VerifyBlame` in processor/src/key_gen.rs:504-556: `accuser`, `accused`, the `share` bytes, and the `blame` proof all originate from a tributary `InvalidDkgShare` transaction published by a validator (the accuser). `accuser`/`accused` are deserialized as `Participant` (nonzero u16) in coordinator/src/tributary/transaction.rs:346-353 with no `<= n` bound in the read path, and are forwarded verbatim into `AdditionalBlameMachine::blame`. Inside `decrypt_with_proof`, `self.enc_keys[&decryptor]` panics on a missing key, and `self.commitments[&sender]` panics identically in `blame_internal`. Additionally, because `VerifyBlame` only requires the `share` to deserialize (lines 507-522) — not to correspond to the accused — an accuser can pair honest-looking ciphertext with a wrong participant index.

A second consequence of the missing hook: `AdditionalBlameMachine::new` never re-verifies the stored commitment PoKs, so if `CommitmentsDb` holds commitments whose `EncryptionKeyMessage` round-1 PoK was never confirmed for this attempt (the processor writes `CommitmentsDb::set` at key_gen.rs:359 before verification completes and only error-maps failures), blame attribution can be computed against unlabeled commitments.

### Impact Explanation

An unprivileged participant who can publish an `InvalidDkgShare` tributary transaction can supply an `accuser`/`accused` index `> params.n()`. The processor then panics inside `decrypt_with_proof`/`blame_internal` on the `enc_keys`/`commitments` `HashMap` index — a remote crash of the processor's blame-verification path, directly analogous to the NULL-SID crash in the advisory (availability impact, medium severity). Where a panic is avoided, blame can be evaluated against commitments that never passed the round-1 validation hook, producing `ProcessorMessage::Blame` against the wrong party and causing a fatal-slash of an honest validator — misattributed, undocumented blame from public inputs.

### Likelihood Explanation

Any validator able to get an `InvalidDkgShare` transaction onto the tributary triggers this path; it needs only a malformed participant index in an otherwise parseable message. No collusion, key material, or timing is required. The panic does not require the DKG to have succeeded — `VerifyBlame` reads params and stored commitments from the DB and constructs `AdditionalBlameMachine` unconditionally (key_gen.rs:505-556).

### Recommendation

In `AdditionalBlameMachine::new` and/or `blame`/`decrypt_with_proof`:

- Reject `sender`/`recipient`/`decryptor` values `> params.n()` (or not present in `enc_keys`) with a defined error instead of indexing `HashMap`s with `[]`.
- Re-run the `verify_r1` hook checks inside `AdditionalBlameMachine::new`: enforce `commitments.len() == t` and verify the PoK `SchnorrSignature` under `challenge::<C>(context, i, sig.R, cached_msg)` before registering the key — i.e., label the object at creation, as XACE does.
- In the processor (`VerifyBlame` handler), validate `accuser` and `accused` against `params` before use and treat blame on unvalidated commitment sets as inadmissible rather than defaulting to blaming the accuser.

### Proof of Concept

```rust
// Attacker is a tributary validator. During a DKG attempt they publish:
Transaction::InvalidDkgShare {
  attempt,
  accuser:  Participant::new(params.n() + 10).unwrap(), // > n, validly nonzero
  accused:  Participant::new(1).unwrap(),
  share:    <any well-formed EncryptedMessage bytes>,
  blame:    None,
  signed:   <valid signature by the attacker>,
}

// Processor path (processor/src/key_gen.rs:504+):
//   EncryptedMessage::read succeeds -> commitment msgs loaded ->
//   AdditionalBlameMachine::new(context, params.n(), msgs).unwrap()
//   .blame(accuser, accused, share, None)
// -> blame_internal -> decrypt_with_proof indexes
//    self.enc_keys[&decryptor] where decryptor = accuser = n + 10
// -> HashMap indexing panic (equivalent to NULL SID dereference),
//    crashing the processor's message handling.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crypto/dkg/pedpop/src/lib.rs (L313-334)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

      if msg.commitments.len() != self.params.t().into() {
        Err(PedPoPError::InvalidCommitments(l))?;
      }

      // Step 5: Validate each proof of knowledge
      // This is solely the prep step for the latter batch verification
      msg.sig.batch_verify(
        rng,
        &mut batch,
        l,
        msg.commitments[0],
        challenge::<C>(self.context, l, msg.sig.R.to_bytes().as_ref(), &msg.cached_msg),
      );

      commitments.insert(l, msg.commitments.drain(..).collect::<Vec<_>>());
    }

    batch.verify_vartime_with_vartime_blame().map_err(PedPoPError::InvalidCommitments)?;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L649-662)
```rust
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

**File:** coordinator/src/tributary/transaction.rs (L341-353)
```rust
      3 => {
        let mut attempt = [0; 4];
        reader.read_exact(&mut attempt)?;
        let attempt = u32::from_le_bytes(attempt);

        let mut accuser = [0; 2];
        reader.read_exact(&mut accuser)?;
        let accuser = Participant::new(u16::from_le_bytes(accuser))
          .ok_or_else(|| io::Error::other("invalid participant in InvalidDkgShare"))?;

        let mut faulty = [0; 2];
        reader.read_exact(&mut faulty)?;
        let faulty = Participant::new(u16::from_le_bytes(faulty))
```
