### Title
Out-of-range Participant in blame request causes unrecoverable panic (process crash DoS) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept attacker-supplied `sender` and `recipient` `Participant` values and index `HashMap`s with them via `self.commitments[&sender]` and `self.enc_keys[&decryptor]` (encryption.rs). `Participant` only enforces `!= 0`, so any `Participant` not in `1..=n` — or any participant whose commitment message wasn't registered — triggers a panic inside `Index for HashMap`, crashing the calling process. This is a remotely triggerable denial of service, matching the bug class of CVE-2020-2924 (unauthenticated/low-cost input causing a complete crash of the server component).

### Finding Description
`Participant::new` accepts any non-zero `u16`, so indexes `n+1..=u16::MAX` are representable [1](#0-0) . In `blame_internal`, both the commitments map and the encryption-key map are indexed with the caller-supplied participant IDs:

- `self.commitments[&sender]` when evaluating share-verification statements [2](#0-1) 
- `self.enc_keys[&decryptor]` inside `decrypt_with_proof` [3](#0-2) 

`AdditionalBlameMachine::new` only populates these maps for `i in 1..=n` [4](#0-3) , and neither `blame` nor `blame_internal` validates that `sender`/`recipient` are within `1..=n` [5](#0-4) . Indexing a `HashMap` with `map[&key]` panics on a missing key, so `blame(accuser, accused, ...)` with `accused = Participant(n+1)` (or `accuser = Participant(n+1)` reaching `commitments[&sender]`) panics.

The reachable driver exists in the processor: `CoordinatorMessage::VerifyBlame { accuser, accused, share, blame }` feeds untrusted, deserialized `Participant` values straight into `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` [6](#0-5) . The upstream deserializer (`Transaction::InvalidDkgShare`) only checks that the participant is non-zero via `Participant::new`, not that it is `<= n` [7](#0-6) . The doc comment on `AdditionalBlameMachine::new` itself acknowledges that unexpected inputs "may cause everything from inaccurate blame to panics" [8](#0-7) .

A secondary instance of the same class: `Interpolation::interpolation_factor` for `Interpolation::Constant` indexes `c[i-1]` unconditionally [9](#0-8) , and `ThresholdView::verification_share` / `original_verification_share` panic on any participant not in the map [10](#0-9) . For `Interpolation::Lagrange`, duplicate `included` entries (reachable via `ThresholdView::interpolation_factor` on a caller-built or deserialized view, since `included` is a `Vec<Participant>` supplied externally) make `denom` zero and `denom.invert().unwrap()` panic [11](#0-10)  — the "safe" comment relies on a caller-side precondition that `ThresholdView::interpolation_factor` does not enforce.

### Impact Explanation
A single malformed blame transaction/message causes a panic in the processor's blame-verification path. In Serai's deployment the processor/coordinator runs as a binary where an unwinding panic in a message handler aborts the task — and the codebase itself installs panic hooks that call `std::process::exit(1)` on task panic (seen in the relayer), so this is a complete, repeatable process crash, exactly the "hang or frequently repeatable crash (complete DOS)" impact class. Because the crash occurs while handling a consensus-relevant message, an attacker can repeatedly crash nodes that process blame reports, halting DKG/signing progress. No secret material is needed; the attacker's only requirement is getting an `InvalidDkgShare`-style message with a syntactically valid but out-of-range `Participant` to a `VerifyBlame` handler.

### Likelihood Explanation
Reachability requires only public/serialized inputs: `accuser` and `accused` are `Participant(u16)` fields read from an untrusted transaction/message and validated only for non-zero. The cost is minimal (a few crafted bytes). The only moderating factor is that blame evaluation is invoked after a DKG exists and requires the surrounding protocol to route the accusation — but no privilege, threshold, or valid cryptographic material is required to trigger the panic, since the panic happens during map lookup before any cryptographic verdict is produced. Severity: Medium (availability-only, no secret leakage or forgery), consistent with the reference CVSS 4.9.

### Recommendation
- In `blame_internal` / `decrypt_with_proof`, replace `map[&key]` indexing with `map.get(&key).ok_or(...)`/`else`-based fault attribution, treating an unknown `sender`/`recipient` as a structured error (e.g., blame the accuser or return a dedicated `InvalidParticipant` error).
- In `AdditionalBlameMachine::new` and `BlameMachine::blame`, explicitly reject `sender`/`recipient` values `> n` (or not present in the maps) before any indexing.
- Similarly harden `ThresholdView::verification_share`, `original_verification_share`, `interpolation_factor` (bounds-check `c[i-1]`, dedupe `included` or return `Option`), since they are public APIs that panic on adversarial input.

### Proof of Concept
```rust
// crypto/dkg/pedpop — conceptual PoC
// params: n = 3, t = 2. Build an AdditionalBlameMachine with valid
// commitment messages for participants 1..=3.
let machine = AdditionalBlameMachine::<Ristretto>::new(context, 3, commitment_msgs).unwrap();

// Craft a blame where `accused` is a non-zero Participant outside 1..=n.
let sender = Participant::new(1).unwrap();
let recipient = Participant::new(4).unwrap(); // 4 > n, yet valid Participant

// Any syntactically readable EncryptedMessage whose pop verifies (or even
// reaches the enc_keys lookup) triggers:
//   decryption.rs: `self.enc_keys[&decryptor]` -> HashMap index panic
// or, with proof=None/InvalidSignature path skipped, `commitments[&sender]`
// with sender = Participant(4) panics identically.
machine.blame(sender, recipient, msg, Some(proof)); // panic: key not found
```
The same panic is reachable end-to-end by submitting an `InvalidDkgShare`/blame accusation whose `accuser`/`faulty` field is `Participant(n+1)` — the transaction reader accepts it (only checks non-zero), and `VerifyBlame` in the processor calls `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)` unconditionally [12](#0-11) .

### Citations

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

**File:** crypto/dkg/src/lib.rs (L226-229)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
      Interpolation::Lagrange => {
```

**File:** crypto/dkg/src/lib.rs (L239-247)
```rust
          let share = F::from(u64::from(u16::from(*l)));
          num *= share;
          denom *= share - i_f;
        }

        // Safe as this will only be 0 if we're part of the above loop
        // (which we have an if case to avoid)
        num * denom.invert().unwrap()
      }
```

**File:** crypto/dkg/src/lib.rs (L672-682)
```rust
  pub fn original_verification_share(&self, l: Participant) -> C::G {
    self.original_verification_shares[&l]
  }

  /// Return the interpolated verification share, with the expected linear combination taken,
  /// for the specified participant.
  ///
  /// This will panic if the participant was not included in the signing set.
  pub fn verification_share(&self, l: Participant) -> C::G {
    self.verification_shares[&l]
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-603)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
      ))
      .is_identity(),
    ) {
```

**File:** crypto/dkg/pedpop/src/lib.rs (L643-648)
```rust
  /// messages.
  ///
  /// This constructor assumes the full validity of the commitment messages. They must be fully
  /// authenticated as having come from the supposed party and verified as valid. Usage of invalid
  /// commitments is considered undefined behavior, and may cause everything from inaccurate blame
  /// to panics.
```

**File:** crypto/dkg/pedpop/src/lib.rs (L654-661)
```rust
    let mut commitments = HashMap::new();
    let mut encryption = Decryption::new(context);
    for i in 1 ..= n {
      let i = Participant::new(i).unwrap();
      let Some(msg) = commitment_msgs.remove(&i) else { Err(PedPoPError::MissingParticipant(i))? };
      commitments.insert(i, encryption.register(i, msg).commitments);
    }
    Ok(AdditionalBlameMachine(BlameMachine { commitments, encryption, result: None }))
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

**File:** processor/src/key_gen.rs (L504-513)
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
```

**File:** processor/src/key_gen.rs (L543-549)
```rust
        let substrate_blame = AdditionalBlameMachine::new(
          context(&id, SUBSTRATE_KEY_CONTEXT),
          params.n(),
          substrate_commitment_msgs,
        )
        .unwrap()
        .blame(accuser, accused, substrate_share, substrate_blame);
```

**File:** coordinator/src/tributary/transaction.rs (L346-353)
```rust
        let mut accuser = [0; 2];
        reader.read_exact(&mut accuser)?;
        let accuser = Participant::new(u16::from_le_bytes(accuser))
          .ok_or_else(|| io::Error::other("invalid participant in InvalidDkgShare"))?;

        let mut faulty = [0; 2];
        reader.read_exact(&mut faulty)?;
        let faulty = Participant::new(u16::from_le_bytes(faulty))
```
