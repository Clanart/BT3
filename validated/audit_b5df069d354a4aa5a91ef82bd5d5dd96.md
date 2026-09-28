### Title
`GeneratorPromotion::complete` accepts an out-of-domain `proofs` entry (our own index) and then panics on the actually-missing participant - ([File: crypto/dkg/promote/src/lib.rs])

### Summary
The Xen bug (CVE-2020-25600) is a limit/bound validated under the wrong set of assumptions: out-of-range slots passed validation because the bound was recorded before the domain's true shape was applied, so resources that should have been rejected were accepted and later corrupted adjacent state. In `dkg-promote`, `GeneratorPromotion::complete` validates the `proofs` map under the assumption "n-1 entries, each ≤ n" — but the required domain is actually `1..=n` **excluding `params.i()`**. A `proofs` map containing our own participant index satisfies both recorded checks, is accepted, and then `.unwrap()` panics when the genuinely-required participant's proof is missing. The Xen limit was wrong for the same structural reason: validated against a superset before the actual constraint was applied.

### Finding Description
`complete` checks only the quantity and the upper bound of the supplied proofs:

```rust
// crypto/dkg/promote/src/lib.rs
if proofs.len() != (usize::from(params.n()) - 1) { ... IncorrectAmountOfParticipants ... }
for i in proofs.keys().copied() {
  if u16::from(i) > params.n() { ... InvalidParticipant ... }
}
``` [1](#0-0) 

Neither check verifies that `proofs` does **not** contain `params.i()` nor that it contains every `i ∈ 1..=n, i != params.i()`. Immediately afterward the code assumes that corrected domain:

```rust
for i in 1 ..= params.n() {
  let i = Participant::new(i).unwrap();
  if i == params.i() { continue; }
  let proof = proofs.get(&i).unwrap();   // panics if i was displaced
``` [2](#0-1) 

A participant `j` submitting a `proofs` map of exactly `n - 1` entries that includes `params.i()` and excludes honest participant `k` (both `params.i()` and `k` are ≤ n) passes validation, then hits `proofs.get(&k).unwrap()` → panic. This is the same failure shape as XSA-342: an index that is valid under the wrongly-recorded bound (≤ n, count n-1) is accepted even though it is out of bounds for the real structure (the set `1..=n \ {i}`), and subsequent use — indexing `proofs` — faults.

A related unchecked-index panic exists in `AdditionalBlameMachine::blame` → `blame_internal`, which indexes `self.enc_keys[&decryptor]` and `self.commitments[&sender]` without verifying `sender`/`recipient ≤ n`. `Participant::new` only rejects 0 (crypto/dkg/src/lib.rs:29-35), so any attacker-supplied `Participant` in `(n, u16::MAX]` is "valid" yet out of bounds for these maps → panic. [3](#0-2) [4](#0-3) 

### Impact Explanation
A single malformed participant in the promotion protocol aborts every honest party's `complete()` call via panic (DoS — the same impact class as the Xen advisory, which is Medium). For `blame_internal`, an accuser/accused index > n (reachable through `AdditionalBlameMachine::blame`/`BlameMachine::blame` with attacker-chosen `sender`/`recipient`) panics the verifying node, preventing blame adjudication. In the Serai deployment (`processor/src/key_gen.rs` `VerifyBlame` handler calls `AdditionalBlameMachine::new(...).blame(accuser, accused, ...)`), `accuser`/`accused` arrive from a tributary `InvalidDkgShare` transaction — the coordinator bounds-checks them first, but the library itself does not enforce the real domain, so any other integrator calling these public APIs with attacker-influenced `Participant` values gets a crash instead of a clean `PedPoPError`.

### Likelihood Explanation
Low-to-moderate. Triggering `complete` panic requires only that a participating party hand over a `proofs` map containing the completer's own index — trivially constructible since `Participant` for the victim is public. The blame-path panic requires reaching `blame`/`decrypt_with_proof` with an index > n; in the in-repo coordinator path the index is pre-validated against the spec, so exploitation there depends on that upstream check, but the library API is public and the panic is unconditional once an OOB index is supplied.

### Recommendation
In `GeneratorPromotion::complete`, validate the actual domain: reject `proofs` containing `params.i()`, and instead of `proofs.get(&i).unwrap()`, return `PromotionError::InvalidParticipant`/`MissingParticipant` when a required participant's proof is absent. In `blame_internal`/`decrypt_with_proof`, return a `PedPoPError::InvalidParticipant` when `sender`/`recipient`/`decryptor` exceeds `n` instead of indexing the `HashMap`s directly (`self.commitments[&sender]`, `self.enc_keys[&decryptor]`).

### Proof of Concept
```rust
// Given promotion for participant i with params (t = n = 5, i = 3):
// Attacker supplies proofs keyed {1, 2, 3, 4} (includes victim's own i=3, omits 5).
// proofs.len() == 4 == n - 1  -> passes IncorrectAmountOfParticipants
// all keys <= 5               -> passes InvalidParticipant loop
// loop at line 140 skips i == 3, then proofs.get(&5).unwrap() -> panic
let mut bad_proofs = clone_without(&proofs, &Participant::new(5).unwrap());
bad_proofs.insert(
    Participant::new(3).unwrap(),                 // victim's own index
    proofs[&Participant::new(4).unwrap()].clone(), // any well-formed proof
);
// promotions[3].complete(&bad_proofs) -> panic at crypto/dkg/promote/src/lib.rs:146
```
Similarly, `AdditionalBlameMachine::new(ctx, n, msgs).unwrap().blame(Participant::new(n + 1).unwrap(), recipient, msg, None)` panics at `self.commitments[&sender]` (crypto/dkg/pedpop/src/lib.rs:599).

Caveat: I verified the panic paths by inspection; I did not fully trace whether an integrator forwards raw attacker-chosen `Participant` values into `blame`/`decrypt_with_proof` without pre-validation in every call path (the coordinator does pre-validate). The `complete` panic, however, follows directly from the library's own acceptance of the malformed map.

### Citations

**File:** crypto/dkg/promote/src/lib.rs (L125-136)
```rust
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
```

**File:** crypto/dkg/promote/src/lib.rs (L140-156)
```rust
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
      verification_shares.insert(i, proof.share);
    }
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L366-397)
```rust
  pub(crate) fn decrypt_with_proof<E: Encryptable>(
    &self,
    from: Participant,
    decryptor: Participant,
    mut msg: EncryptedMessage<C, E>,
    // There's no encryption key proof if the accusation is of an invalid signature
    proof: Option<EncryptionKeyProof<C>>,
  ) -> Result<Zeroizing<E>, DecryptionError> {
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
  }
```
