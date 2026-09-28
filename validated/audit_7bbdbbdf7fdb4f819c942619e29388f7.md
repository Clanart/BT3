### Title
Panic on missing encryption-key entry when `BlameMachine::blame` names the local node as recipient (untrusted blame input → DoS) - (crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP DKG's blame adjudication path dereferences a per-participant encryption-key map entry without checking that the key exists. `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` directly (line 388), but `enc_keys` is only populated for *other* participants during round-1 registration — the local participant's own index is never inserted. Any call to `BlameMachine::blame` / `blame_internal` where `recipient` is the local node's own `Participant` index (a value supplied as an argument, i.e., attacker-influencable) triggers a HashMap indexing panic, aborting the node mid-protocol.

### Finding Description
The reported OpenSSL bug class is: during processing of attacker-supplied data, a required field (CRL Number) is dereferenced without checking whether it is present, producing a NULL dereference / crash DoS.

The Serai analog lives in `crypto/dkg/pedpop/src/encryption.rs`:

- `Decryption` holds `enc_keys: HashMap<Participant, C::G>` (line 344).
- Entries are added only via `Decryption::register`, which is called from `SecretShareMachine::verify_r1` (pedpop/src/lib.rs:315) only for participants present in `commitment_msgs` — i.e., everyone *except* `self.params.i()`. The local node's public encryption key lives separately in `Encryption::enc_pub_key` (encryption.rs:442) and is never inserted into `decryption.enc_keys`.
- When adjudicating blame, `Decryption::decrypt_with_proof` computes the DLEq statement `&[self.enc_keys[&decryptor], *proof.key]` at encryption.rs:388. If `decryptor == self.params.i()`, the indexing operator `[]` panics because that key was never registered.

The public, untrusted-input-facing entry point is `BlameMachine::blame(sender, recipient, msg, proof)` (pedpop/src/lib.rs:623) → `blame_internal` (line 575) → `decrypt_with_proof`. Both `sender` and `recipient` are caller-supplied `Participant` values with no validation that `recipient` differs from the local index or exists in `enc_keys`. The analogous participant-facing setters (`validate_map`, `ThresholdParams::new`) carefully validate participant indexes everywhere else in the crate; this map lookup is the one place a "missing required entry" is dereferenced without a check — the same shape as the missing-CRL-Number dereference.

### Impact Explanation
A panic inside `blame` unwinds (or aborts, depending on panic strategy) the node processing the blame claim. Since DKG blame adjudication runs on validator/processor nodes, a malicious participant who submits a blame accusation with `recipient` set to the adjudicating node's own participant index crashes the process before it can either (a) confirm the accused sender or (b) finalize `ThresholdKeys` via `BlameMachine::complete`. This is a remote, input-triggered Denial of Service, mirroring the advisory's crash-DoS impact (analogous to its `AV:N/AC:L/PR:N` reachability within the DKG protocol).

### Likelihood Explanation
Exploitation requires the node to evaluate a blame proof where `recipient` resolves to its own index. `blame` takes `sender`, `recipient`, `msg`, and `proof` as plain arguments; nothing rejects `recipient == params.i()`. Whether a peer can directly cause a coordinator to call `blame` with that argument depends on the surrounding blame-propagation logic (outside the in-scope crates), but the panic itself is a one-line missing-entry dereference triggered purely by argument values — no secrets, no races, no collusion needed. Confirmed-unchecked code path; reachability through the processor layer is plausible but not fully verified within the in-scope sources.

### Recommendation
In `Decryption::decrypt_with_proof` (crypto/dkg/pedpop/src/encryption.rs:388), replace the panicking index with a checked lookup:

```rust
let Some(enc_key) = self.enc_keys.get(&decryptor) else {
  return Err(DecryptionError::InvalidProof);
};
// use *enc_key in the DLEq statement
```

Alternatively, register the local node's own `enc_pub_key` into `decryption.enc_keys` during `Encryption::new` so `blame` on self-recipient claims behaves identically to other participants, or reject `recipient == params.i()` early in `BlameMachine::blame` / `blame_internal` with an explicit error.

### Proof of Concept
Within the in-scope code (no processor harness):

```rust
// After a normal PedPoP run, each honest node holds a BlameMachine.
// A party submits a blame claim naming the adjudicating node as `recipient`:
let our_i = blame_machine_params_i; // BlameMachine::params().i(), a public value
let (machine, faulty) = blame_machine.blame(
    sender,                    // any participant that sent a round-2 EncryptedMessage
    our_i,                     // recipient = the local node's own index
    msg_from_sender_to_anyone, // any EncryptedMessage<C, SecretShare<C::F>>
    Some(attacker_proof),      // any EncryptionKeyProof<C>
);
// blame_internal -> decrypt_with_proof evaluates
//   &[self.enc_keys[&decryptor], *proof.key]
// decryptor == our_i was never inserted into enc_keys (register() is only
// invoked for other participants in verify_r1), so the indexing panics.
```

The panic is deterministic: `enc_keys` contains exactly `n - 1` keys (all participants except `self.params.i()`), because `SecretShareMachine::verify_r1` calls `self.encryption.register(l, msg)` only for `l` drawn from `commitment_msgs`, which `validate_map` guarantees excludes the local index. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L351-362)
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

**File:** crypto/dkg/pedpop/src/lib.rs (L313-316)
```rust
    for l in self.params.all_participant_indexes() {
      let Some(msg) = commitment_msgs.remove(&l) else { continue };
      let mut msg = self.encryption.register(l, msg);

```

**File:** crypto/dkg/pedpop/src/lib.rs (L623-631)
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
```
