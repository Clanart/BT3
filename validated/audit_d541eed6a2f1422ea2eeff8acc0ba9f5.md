### Title
Missing existence check on attacker-controlled participant indexes causes panic in PedPoP blame handling - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` without first checking that the `decryptor` participant ever registered an encryption key. Similarly, `BlameMachine::blame_internal` indexes `self.commitments[&sender]` without checking `sender` is a registered DKG participant. `std::collections::HashMap`'s `Index` impl panics on absent keys, so any party who submits a blame accusation naming an unregistered `recipient` or a `sender` outside the commitment map crashes the node — the same missing-key-validation → crash shape as CVE-2017-9211 (missing key-size check → NULL dereference), mapped onto Serai's HashMap indexing.

### Finding Description
`blame`/`AdditionalBlameMachine::blame` (crypto/dkg/pedpop/src/lib.rs:623-632, 674-682) accept `sender`, `recipient`, an `EncryptedMessage` and an optional `EncryptionKeyProof` — all supplied by the accusing party. They forward to `blame_internal` (lib.rs:575-609), which calls `self.encryption.decrypt_with_proof(sender, recipient, msg, proof)`.

Inside `decrypt_with_proof` (encryption.rs:366-397), the PoP `msg.pop` is verified first (line 374-379). An attacker can trivially satisfy this by constructing the `EncryptedMessage` themselves: they generate `key = k*G`, sign a valid Schnorr PoP for it, and pick `from` as any `Participant`. The check only proves the message is self-consistent — it does not authenticate `from` (that responsibility is documented as the caller's). When a `proof` is present, the DLEq verify at lines 383-389 evaluates `self.enc_keys[&decryptor]` as part of its arguments. If `decryptor` never called `register` (e.g., the accuser names a `Participant` index that isn't in `enc_keys`, such as a non-participant index the caller didn't filter, or a participant who never submitted commitments), `HashMap::index` panics with "key not found".

If `proof` is `None` or the panic is avoided, `blame_internal` then evaluates `self.commitments[&sender]` at lib.rs:599 inside `share_verification_statements`, panicking identically for any `sender` not present in the commitments map (e.g., `Participant` value `> n`, which `Participant::new` permits up to `u16::MAX` independent of `params.n`).

Neither call site in `blame` nor `AdditionalBlameMachine::blame` validates `sender`/`recipient` against the registered participant set before indexing. `calculate_share` does not suffer this because `validate_map` restricts `shares` keys to `all_participant_indexes`, but no equivalent guard exists in the blame path.

### Impact Explanation
A single malicious accusation — reachable whenever the integrator relays blame claims from untrusted counterparties into `blame`/`AdditionalBlameMachine::blame` — panics the process. In Rust this is a controlled abort/unwind rather than a NULL dereference, but the security impact matches the CVE class: a local peer triggers denial of service with crafted public inputs (a validly self-signed `EncryptedMessage` plus out-of-set `Participant` indexes). `AdditionalBlameMachine::new` explicitly notes invalid commitments "may cause everything from inaccurate blame to panics" (lib.rs:648), but the panic here occurs even with fully valid inputs because the *indexes*, not the commitments, are unchecked. Crashing a validator mid-DKG/blame resolution aborts key generation and can stall multisig setup.

### Likelihood Explanation
Exploitation requires a DKG participant (or anyone able to submit blame accusations through the integrator) to call the blame API with an out-of-set `sender`/`recipient`. The blame path exists precisely to adjudicate disputes between untrusted parties, so receiving adversarial inputs is its intended use. The attacker needs no secret material — the PoP key is self-generated — only a validly formed `EncryptedMessage` they can produce themselves. Reachability depends on the integrator passing unvalidated `Participant` values through, which the API invites since `blame` performs no membership check itself.

### Recommendation
In `Decryption::decrypt_with_proof`, replace `self.enc_keys[&decryptor]` with a `.get(&decryptor)` that returns `DecryptionError::InvalidProof` on absence. In `blame_internal`, validate `sender` and `recipient` against `self.commitments`/`enc_keys` (or `params.all_participant_indexes()`) up front and return a defined result for out-of-set indexes rather than indexing the maps. `BlameMachine::blame` should also reject `sender`/`recipient` values exceeding the protocol's `n`.

### Proof of Concept
1. Run a PedPoP `KeyGenMachine`/`SecretShareMachine`/`KeyMachine` flow for `n = 3`, producing a `BlameMachine` (or construct `AdditionalBlameMachine::new(context, 3, commitment_msgs)`).
2. Attacker builds `msg`: pick `k`, set `key = k*G`, Schnorr-sign the PoP challenge with `from = attacker_participant`, encrypt arbitrary share-length bytes.
3. Call `blame(sender = Participant::new(7).unwrap(), recipient = Participant::new(7).unwrap(), msg, Some(proof))` where `7` was never registered — or any `Participant` in `1..=u16::MAX` outside `1..=n`.
4. `decrypt_with_proof` passes `msg.pop.verify` (self-signed), then evaluates `self.enc_keys[&Participant(7)]` → panic: `HashMap` index on absent key. Equivalently, with a `sender` not in `commitments`, `self.commitments[&sender]` at lib.rs:599 panics. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-392)
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
```

**File:** crypto/dkg/pedpop/src/lib.rs (L356-361)
```rust

    // Step 1: Generate secret shares for all other parties
    let mut res = HashMap::new();
    for l in self.params.all_participant_indexes() {
      // Don't insert our own shares to the byte buffer which is meant to be sent around
      // An app developer could accidentally send it. Best to keep this black boxed
```

**File:** crypto/dkg/pedpop/src/lib.rs (L596-600)
```rust
    if !bool::from(
      multiexp_vartime(&share_verification_statements::<C>(
        recipient,
        &self.commitments[&sender],
        Zeroizing::new(share),
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
