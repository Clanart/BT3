### Title
Unauthenticated crash via unguarded `enc_keys` index in blame/decryption path - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Dovecot CVE is an unauthenticated, input-triggered crash (use-after-free leading to availability loss) reachable by sending crafted protocol bytes. The closest Serai analog is a reachable panic on attacker-influenced participant indices inside the PedPoP blame/decryption flow: `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` without checking membership, so any `decryptor` participant index that was never registered causes an unconditional panic, crashing the host processor/coordinator thread.

### Finding Description
`Decryption` stores per-participant encryption keys in a `HashMap<Participant, C::G>` populated by `register`, which only runs for participants whose `EncryptionKeyMessage` was registered [1](#0-0) . In `decrypt_with_proof`, after the per-message Schnorr PoP check, the decryptor's registered key is fetched with a bare index operator: `self.enc_keys[&decryptor]` [2](#0-1) . `HashMap::index` panics on a missing key. The `from`/`decryptor` participant values are attacker-controlled fields of the accusation/blame message (e.g., `CoordinatorMessage::VerifyBlame { id, accuser, accused, share, blame }`, where `accuser` is read from the network message), and the `EncryptedMessage`/`EncryptionKeyProof` bytes are parsed via `EncryptedMessage::read` and `EncryptionKeyProof::read` [3](#0-2) [4](#0-3) . Neither `decrypt_with_proof` nor its callers validate that `decryptor` is within `1..=n` or present in `enc_keys` before the indexing expression is evaluated. `Participant` only guarantees non-zero (`Participant::new` rejects only `0`) [5](#0-4) , so any `decryptor > n`, or a `decryptor` whose encryption key message was never registered (faulty/dropped commitment), reaches the panic.

Note: this is a panic (abort/ unwind) rather than a literal use-after-free, but it is the same bug class in Rust terms — a missing-bounds/membership check in a parser-adjacent code path — and Serai's own code comments show the library's stated expectation "not to panic" [6](#0-5) .

### Impact Explanation
An unprivileged party able to trigger the blame-verification path (a DKG participant submitting an `InvalidShare`/accusation, or any input that causes a `VerifyBlame` message to be produced with an attacker-chosen accuser/decryptor index) can deterministically panic every honest node that processes it. Because the panic fires during blame adjudication, it can both DoS the node and stall the key-generation/slashing pipeline. This mirrors the CVE's impact profile: remote, unauthenticated, availability-only crash (CVSS 5.3, `A:L`).

### Likelihood Explanation
Reachability requires the caller to route an attacker-influenced `Participant` into `decrypt_with_proof` without validation. In `processor/src/key_gen.rs`'s `VerifyBlame` handler, `accuser`/`accused` come from the coordinator message and the parsed `share`/`blame` bytes are fed directly into `EncryptedMessage::read`/`EncryptionKeyProof::read` before `blame(...)` is invoked [7](#0-6) ; no snippeted validation constrains `accuser` to the registered set. The PoP signature check at the top of `decrypt_with_proof` [8](#0-7)  must pass first, so the attacker needs a `share` with a valid PoP — obtainable by any participant generating a real encrypted share — or the panic may also be reached through other internal callers indexing with an unregistered `decryptor`. Certainty is moderate: I could not fully trace every call site of `decrypt_with_proof`/`AdditionalBlameMachine::blame` in the available iterations, so exploitability depends on whether upstream code ever bounds-checks the accuser index; nothing in the inspected code does.

### Recommendation
Replace `self.enc_keys[&decryptor]` with `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?` (or a dedicated `UnknownParticipant` error) so a missing/unregistered decryptor yields an `Err` instead of a panic. Similarly, validate `from`/`decryptor` ∈ `1..=n` at the top of `decrypt_with_proof` and in `AdditionalBlameMachine::blame` before any map indexing.

### Proof of Concept
```rust
// crypto/dkg/pedpop context: build a Decryption map with keys for
// participants 1..=n, then call the blame/decryption path with a
// decryptor index outside the registered set.
let mut dec = Decryption::<Ristretto>::new(context);
// register only honest participants 1..=n
for i in 1..=n { dec.register(Participant::new(i).unwrap(), msg_i); }

// Attacker-influenced VerifyBlame supplies decryptor = n + 1 (a valid
// non-zero Participant that was never registered) plus a message whose
// PoP verifies (e.g., a real EncryptedMessage the attacker generated).
let decryptor = Participant::new(n + 1).unwrap();
// Inside decrypt_with_proof:
//   proof.dleq.verify(..., &[self.enc_keys[&decryptor], *proof.key])
//                                    ^^^^^^^^^^^^^^^ panics: key not found
dec.decrypt_with_proof(from, decryptor, msg, Some(proof)); // -> panic
```
Expected result: thread panic on `HashMap` index (`enc_keys[&decryptor]`), crashing the processor — matching the remote-crash class of ALPINE-CVE-2020-10958.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-177)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L267-269)
```rust
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<Self> {
    Ok(Self { key: Zeroizing::new(C::read_G(reader)?), dleq: DLEqProof::read(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-361)
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
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
```

**File:** crypto/dkg/src/lib.rs (L29-35)
```rust
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }
```

**File:** crypto/frost/src/curve/mod.rs (L86-88)
```rust
  // We could still panic on the 0-hash, preferring correctness to liveliness. Finding the 0-hash
  // is as computationally complex as simply calculating the group key's discrete log however,
  // making it not worth having a panic (as this library is expected not to panic).
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
