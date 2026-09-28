### Title
Out-of-range `Participant` index in PedPoP blame path panics on `HashMap` indexing (denial of service) - (crypto/dkg/pedpop/src/encryption.rs:388, crypto/dkg/pedpop/src/lib.rs:599)

### Summary
The CVE is a crash (invalid memory dereference → segfault) reachable via an attacker-crafted input hitting an unhandled edge case. The Serai analog is an unhandled-participant edge case in the PedPoP blame-evaluation path: `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal` index `enc_keys`/`commitments` maps with `HashMap[key]` syntax on `Participant` values that are never validated against `n`. A blame accusation naming an accuser or accused with index `> n` (any nonzero `u16` deserializes to a valid `Participant`) causes a panic, crashing the node handling the blame — a remotely triggerable denial of service.

### Finding Description
`Participant::new` accepts any nonzero `u16`, and the blame APIs take `sender`/`recipient` (`accused`/`accuser`) directly:

- `Decryption::decrypt_with_proof` evaluates `self.enc_keys[&decryptor]` when a proof is supplied. `enc_keys` only contains entries for `1..=n` (populated in `AdditionalBlameMachine::new` or via `register`). A `decryptor`/`accuser` index `> n` panics immediately. [1](#0-0) 
- `BlameMachine::blame_internal` evaluates `self.commitments[&sender]` when the decrypted share parses as a canonical scalar. `commitments` only contains `1..=n`, so an `accused`/`sender` index `> n` panics. [2](#0-1) 

Neither `BlameMachine::blame` nor `AdditionalBlameMachine::blame` validates that `sender` and `recipient` are within `1..=n`; they pass the values straight through. [3](#0-2) [4](#0-3) 

In the processor's blame flow (`CoordinatorMessage::VerifyBlame`), `accuser` and `accused` come from the message and are fed to `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` with no range check against `params.n()`. The `blame` bytes are also attacker-controlled (`EncryptionKeyProof::read` of attacker bytes). A peer submits a blame request with `accuser > n` plus any parseable proof → `enc_keys[&accuser]` panics. [5](#0-4) 

Note: this is a panic in Rust (`HashMap` index on missing key), not memory unsafety — the code is safe Rust. If index-size limits mean some file contents were unavailable for confirming the processor-side validation, the core library panic in the in-scope `pedpop` crate stands regardless.

### Impact Explanation
A panic in `blame`/`blame_internal` unwinds or aborts the node processing blame verification. Since blame evaluation runs on peers/coordinator components during DKG fault resolution, a single crafted blame message naming a nonexistent participant crashes the process — denial of service of the key-generation/slashing flow, matching the CVE's crash-only impact class.

### Likelihood Explanation
Reachable by any party able to submit a blame accusation (a DKG participant or anyone whose messages reach the blame-verification path). `Participant` is a plain nonzero `u16`; picking `n+1` requires no cryptographic work. The proof only needs to *parse* (`EncryptionKeyProof::read` of well-formed bytes) since the panic occurs during argument evaluation before DLEq verification. No collusion, leaked keys, or malicious-validator assumptions needed — only the ability to send blame bytes.

### Recommendation
In `Decryption::decrypt_with_proof` and `BlameMachine::blame_internal`, replace `map[&key]` indexing with `map.get(&key)` and return the appropriate fault/`DecryptionError` (e.g., treat a nonexistent `sender`/`recipient` as the faulty accusing party or a distinct error). Alternatively, validate `sender`/`recipient` against `1..=n` at the top of `BlameMachine::blame` and `AdditionalBlameMachine::blame`.

### Proof of Concept
1. Build an `AdditionalBlameMachine` via `AdditionalBlameMachine::new(context, n, commitment_msgs)` for any `n`.
2. Construct `EncryptedMessage::<C, SecretShare<C::F>>` bytes: any `C::read_G`-valid `key`, any parseable `SchnorrSignature`, and a `SecretShare` repr of a canonical scalar (e.g., `C::F::ONE.to_repr()`).
3. Construct parseable `EncryptionKeyProof` bytes (any `read_G`-valid key + any `DLEqProof`).
4. Call `machine.blame(sender, recipient, msg, Some(proof))` with `recipient = Participant::new(n + 1)` → panics at `self.enc_keys[&decryptor]` (`encryption.rs:388`). Or with `sender = Participant::new(n + 1)`, a valid PoP binding `from = n+1`, and a canonical-but-invalid share → panics at `self.commitments[&sender]` (`lib.rs:599`).

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L383-390)
```rust
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-604)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
      return sender;
```

**File:** crypto/dkg/pedpop/src/lib.rs (L623-632)
```rust
  pub fn blame(
    self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> (AdditionalBlameMachine<C>, Participant) {
    let faulty = self.blame_internal(sender, recipient, msg, proof);
    (AdditionalBlameMachine(self), faulty)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L674-682)
```rust
  pub fn blame(
    &self,
    sender: Participant,
    recipient: Participant,
    msg: EncryptedMessage<C, SecretShare<C::F>>,
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Participant {
    self.0.blame_internal(sender, recipient, msg, proof)
  }
```

**File:** processor/src/key_gen.rs (L504-549)
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
```
