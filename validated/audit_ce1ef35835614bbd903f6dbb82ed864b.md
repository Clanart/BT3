### Title
Unregistered `Participant` index in blame/decrypt path panics via unchecked `HashMap` indexing — (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to `op_panic` (a reachable entry point that converts untrusted input into a panic in the thread hosting the code), `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` and `blame_internal` indexes `self.commitments[&sender]` without checking membership. Both `sender` and `recipient` are attacker-influenceable `Participant` arguments to the public `BlameMachine::blame` / `AdditionalBlameMachine::blame` APIs. Supplying a `Participant` index that was never registered causes a `HashMap` index panic, aborting the calling thread.

### Finding Description
`Decryption::register` only inserts keys for participants that provided an `EncryptionKeyMessage`:

```rust
// crypto/dkg/pedpop/src/encryption.rs
self.enc_keys.insert(participant, msg.enc_key);
```

But `decrypt_with_proof` later performs an unchecked index:

```rust
// crypto/dkg/pedpop/src/encryption.rs:388
&[self.enc_keys[&decryptor], *proof.key],
```

`HashMap`'s `Index` impl panics on a missing key. This is reached from `blame_internal` in `crypto/dkg/pedpop/src/lib.rs`, which is exposed publicly via `BlameMachine::blame` and `AdditionalBlameMachine::blame`:

```rust
// crypto/dkg/pedpop/src/lib.rs
let share_bytes = match self.encryption.decrypt_with_proof(sender, recipient, msg, proof) { ... }
// ...
multiexp_vartime(&share_verification_statements::<C>(
    recipient,
    &self.commitments[&sender],   // second unchecked index, line ~599
```

Reachability: `AdditionalBlameMachine::new` registers `enc_keys` only for `i in 1 ..= n`. A caller (or a protocol participant able to trigger a blame evaluation, e.g. a malicious validator submitting an accusation naming an out-of-range `recipient`/`sender` `Participant` such as `n + 1`, which `Participant::new` accepts since it only rejects zero) causes `blame_internal` to hit `self.enc_keys[&decryptor]` whenever `proof` is `Some(...)`, or `self.commitments[&sender]` when decryption succeeds. Neither `blame` nor `blame_internal` validates that `sender`/`recipient` are members of the registered set before indexing.

### Impact Explanation
A panic in the containing thread aborts the DKG/blame-resolution flow and, in a runtime embedding these machines (e.g. a coordinator/processor thread handling blame proofs), kills the thread processing the accusation — the exact "panic in the runtime's containing thread" shape of the `op_panic` advisory. This is a denial of service against the participant running the blame machine, preventing completion or fault attribution of the key-generation protocol.

### Likelihood Explanation
Medium-low to moderate. It requires reaching a blame evaluation (`calculate_share` already errored or an external `AdditionalBlameMachine` is in use) and controlling the `sender`/`recipient` arguments — plausible in a system where accusations are relayed between mutually distrustful parties, since `Participant` is just a non-zero `u16` and no membership check is performed against the registered map. It does not leak key material; impact is availability only, consistent with the Medium severity of the source advisory.

### Recommendation
Replace unchecked indexing with membership checks:

```rust
let Some(dec_key) = self.enc_keys.get(&decryptor) else {
    return Err(DecryptionError::InvalidProof); // or blame the accuser
};
```

and in `blame_internal`, fetch `self.commitments.get(&sender)` and return `sender` (or an explicit error) when absent, so that accusations involving unknown participants resolve to blame rather than panicking.

### Proof of Concept
```rust
// Setup: an AdditionalBlameMachine built for n = 3 participants.
let machine = AdditionalBlameMachine::<Secp256k1>::new(context, 3, commitment_msgs).unwrap();

// An accusation naming recipient Participant(4), which was never registered,
// with a proof present.
let recipient = Participant::new(4).unwrap();
machine.blame(sender, recipient, msg, Some(proof));
// Panics inside Decryption::decrypt_with_proof at:
//   self.enc_keys[&decryptor]
// thread panics: "no entry found for key"
```

Equivalently, `blame` with a `sender` outside `1 ..= n`, a valid `proof`, and a ciphertext that decrypts to a canonical scalar reaches `self.commitments[&sender]` in `blame_internal` (`crypto/dkg/pedpop/src/lib.rs`) and panics the same way.