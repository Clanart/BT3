### Title
Malformed `GeneratorPromotion::complete` proof set can omit a required participant and crash on `unwrap` - (File: crypto/dkg/promote/src/lib.rs)

### Summary
`GeneratorPromotion::complete` validates only the count and upper bound of the supplied `proofs` map, then indexes it while iterating `1 ..= n` and skipping only `params.i()`. Because it never rejects a proof keyed by `params.i()` nor verifies the key set equals `{1..=n} \ {i}`, a map can have the accepted length `n - 1` while still missing a required participant, causing `proofs.get(&i).unwrap()` to panic. [1](#0-0) 

### Finding Description
The function first requires `proofs.len() == n - 1` and that every key is `<= n`, but does not check that the keys are exactly all participants except the local participant. [2](#0-1)  It then inserts the local share under `params.i()`, iterates every participant `1 ..= n`, skips `params.i()`, and unconditionally unwraps `proofs.get(&i)` for all remaining indexes. [3](#0-2)  A malformed set such as `{ self_i, other_i }` for `n = 3`, `i = 1` passes the count/range checks yet omits participant `3`, so the loop reaches `proofs.get(&Participant(3)).unwrap()` and panics. [4](#0-3)  The individual proof messages are untrusted serializable data via `GeneratorProof::read`. [5](#0-4) 

### Impact Explanation
This is a remote availability failure analogous to the malformed-packet assertion crash: an otherwise well-formed promotion session can be aborted by a panic after input validation has run. [1](#0-0)  The panic occurs before `ThresholdKeys::new` is reached, so the promoter cannot obtain the promoted key set and any caller that does not catch panics crashes. [6](#0-5) 

### Likelihood Explanation
Any deployment that aggregates `GeneratorProof`s by a claimed participant index, or otherwise allows a participant to submit a set containing an extra/self index instead of the required missing index, can trigger this with only public protocol messages. [5](#0-4)  The bypass is structural rather than cryptographic: no valid proof is needed for the omitted participant because the panic happens while trying to fetch it. [7](#0-6) 

### Recommendation
Replace the count/range checks with exact set validation: require `proofs.keys().collect::<HashSet<_>>() == params.all_participant_indexes().filter(|i| *i != params.i()).collect()`, reject `params.i()` in `proofs`, and return `PromotionError` instead of using `unwrap`. [8](#0-7)  Also bind each proof to the authenticated sender/participant index before inserting it into the map so a peer cannot supply entries under another participant’s index. [9](#0-8) 

### Proof of Concept
Conceptually, for `n = 3`, local participant `i = 1`, construct a valid `GeneratorPromotion` from `base`, then supply `proofs = { Participant(1): arbitrary_valid_encoding, Participant(2): valid_proof_for_2 }`. The map passes `len() == n - 1 == 2` and all keys are `<= 3`; the loop skips `1`, verifies `2`, then panics on missing `3` at `proofs.get(&i).unwrap()`. [8](#0-7)

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L52-56)
```rust
fn transcript<G: GroupEncoding>(key: &G, i: Participant) -> RecommendedTranscript {
  let mut transcript = RecommendedTranscript::new(b"DKG Generator Promotion v0.2");
  transcript.append_message(b"group_key", key.to_bytes());
  transcript.append_message(b"participant", i.to_bytes());
  transcript
```

**File:** crypto/dkg/promote/src/lib.rs (L72-76)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorProof<C>> {
    Ok(GeneratorProof {
      share: <C as Ciphersuite>::read_G(reader)?,
      proof: DLEqProof::read(reader)?,
    })
```

**File:** crypto/dkg/promote/src/lib.rs (L120-154)
```rust
  pub fn complete(
    self,
    proofs: &HashMap<Participant, GeneratorProof<C1>>,
  ) -> Result<ThresholdKeys<C2>, PromotionError> {
    let params = self.base.params();
    if proofs.len() != (usize::from(params.n()) - 1) {
      Err(PromotionError::IncorrectAmountOfParticipants {
        t: params.n(),
        n: params.n(),
        amount: proofs.len() + 1,
      })?;
    }
    for i in proofs.keys().copied() {
      if u16::from(i) > params.n() {
        Err(PromotionError::InvalidParticipant { n: params.n(), participant: i })?;
      }
    }

    let mut verification_shares = HashMap::new();
    verification_shares.insert(params.i(), self.proof.share);
    for i in 1 ..= params.n() {
      let i = Participant::new(i).unwrap();
      if i == params.i() {
        continue;
      }

      let proof = proofs.get(&i).unwrap();
      proof
        .proof
        .verify(
          &mut transcript(&self.base.original_group_key(), i),
          &[C1::generator(), C2::generator()],
          &[self.base.original_verification_share(i), proof.share],
        )
        .map_err(|_| PromotionError::InvalidProof(i))?;
```

**File:** crypto/dkg/promote/src/lib.rs (L158-166)
```rust
    Ok(
      ThresholdKeys::new(
        params,
        self.base.interpolation().clone(),
        self.base.original_secret_share().clone(),
        verification_shares,
      )
      .unwrap(),
    )
```
