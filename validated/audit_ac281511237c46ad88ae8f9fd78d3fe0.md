### Title
Unvalidated `recipient` index in PedPoP blame evaluation panics on `enc_keys` HashMap lookup, crashing the evaluating participant - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The external report (CVE-2026-3119) describes a crash in `named` triggered by a correctly *authenticated* input (a validly TSIG-signed TKEY query): the message passes all authentication checks and then reaches code that faults on its contents. The direct Serai analog is in the PedPoP DKG blame machinery: `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` (a `HashMap` lookup that panics on a missing key) after successfully verifying an attacker-controlled Schnorr proof-of-possession, but without ever checking that `decryptor` is a participant who actually registered an encryption key. A DKG participant can therefore crash any honest party evaluating a blame claim by naming a `recipient` outside the registered set — most notably `params.i()` itself, since a party never registers its own encryption key.

### Finding Description
`Decryption::register` only inserts into `enc_keys` when a peer's `EncryptionKeyMessage` is processed. In the normal protocol flow, `SecretShareMachine::verify_r1` calls `self.encryption.register(l, msg)` only for *other* participants (`commitment_msgs` excludes `params.i()` by `validate_map`), so `enc_keys` never contains the local participant index `i`. `AdditionalBlameMachine::new` registers all `1 ..= n`, so any `Participant` index `> n` (or `0` is impossible, but any unused index ≤ u16::MAX) is absent.

Later, `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept an accusation — `sender`, `recipient`, an `EncryptedMessage` (publicly deserializable via `EncryptedMessage::read`, encryption.rs:171), and an `Option<EncryptionKeyProof>` (via `EncryptionKeyProof::read`, encryption.rs:267) — and forward them to `blame_internal` → `decrypt_with_proof`:

- `msg.pop.verify(...)` is checked first (encryption.rs:374-379). An attacker can satisfy this trivially: the PoP challenge binds `from`, `key`, `nonce`, and `msg` (encryption.rs:302-324), and the attacker knows the discrete log of a `key` they generate, so they can produce a valid signature for any claimed `sender` label.
- With `proof: Some(...)`, execution reaches `self.enc_keys[&decryptor]` at encryption.rs:388, which panics when `decryptor` is absent — before any validation of `decryptor` against the participant set.

There is no bounds check anywhere: `blame_internal` (pedpop/src/lib.rs:575-609) passes `recipient` straight through, and `blame` (lib.rs:623-632, 674-682) exposes no validation of `sender`/`recipient` membership either. (`self.commitments[&sender]` at lib.rs:599 is a second, equivalent panic site reached when the share bytes decode but `sender` was never in `commitments`.)

### Impact Explanation
Availability loss matching the CVE class: a correctly-formed, authenticated-protocol message reaches a code path that aborts the process. Any PedPoP participant can broadcast/relay a blame accusation naming a non-registered `recipient` (e.g., the evaluator's own index `i`, or an out-of-range index), and every honest node that evaluates the accusation panics inside `decrypt_with_proof`. In a validator/coordinator deployment this aborts the DKG or signing software at the blame-adjudication step — the exact analog of `named` crashing while processing a valid signed query.

### Likelihood Explanation
Reachable with only public inputs: the attacker needs an `EncryptedMessage` with a self-consistent PoP (they control the ephemeral key, so signing is trivial) and an arbitrary `EncryptionKeyProof` byte string — both are read from untrusted bytes via the documented `read` functions — plus a `recipient` argument outside the registered key set. No collusion, no leaked keys, no malicious validator assumptions beyond being a protocol participant are required. The only precondition is that the deployment calls `blame` on received accusations, which is the documented purpose of `BlameMachine`/`AdditionalBlameMachine`.

### Recommendation
Validate `sender` and `recipient` against the known participant set in `blame_internal`/`decrypt_with_proof` before indexing: replace `self.enc_keys[&decryptor]` (encryption.rs:388) and `self.commitments[&sender]` (pedpop/src/lib.rs:599) with `.get()` returning `DecryptionError`/`PedPoPError` on `None`. Concretely, an unknown `recipient` should blame the accuser-side claim (or error), and an unknown `sender` should blame the accuser, never panic.

### Proof of Concept
```rust
// Setup: participants 1..=n run PedPoP; victim reaches BlameMachine via
// KeyMachine::calculate_share (its enc_keys contains all l != i, never i).

// Attacker (any participant, or accuser in AdditionalBlameMachine usage):
let key = Zeroizing::new(C::random_nonzero_F(rng));
let pub_key = C::generator() * key.deref();
let nonce = Zeroizing::new(C::random_nonzero_F(rng));
let pub_nonce = C::generator() * nonce.deref();
let msg_bytes: SecretShare<C::F> = /* any F::Repr bytes */;
let pop = SchnorrSignature::sign(
  &key, nonce,
  pop_challenge::<C>(context, pub_nonce, pub_key, sender, msg_bytes.as_ref()),
);
let msg = EncryptedMessage { key: pub_key, pop, msg: Zeroizing::new(msg_bytes) };

// Arbitrary bytes parse as an EncryptionKeyProof if they encode valid points/scalars
let proof = Some(EncryptionKeyProof::<C>::read(&mut crafted_bytes).unwrap());

// Victim evaluates the accusation; decryptor = victim's own i (never registered),
// or any Participant index outside the registered set:
blame_machine.blame(sender, victim_i, msg, proof);
// pop.verify passes -> encryption.rs:388 `self.enc_keys[&decryptor]` -> panic!
```

Key code references: `Decryption::decrypt_with_proof` indexing `enc_keys[&decryptor]` at `crypto/dkg/pedpop/src/encryption.rs:381-396` [1](#0-0) ; `enc_keys` registration excluding the local participant at `crypto/dkg/pedpop/src/lib.rs:313-336` [2](#0-1) ; unvalidated `sender`/`recipient` forwarded in `blame_internal` at `crypto/dkg/pedpop/src/lib.rs:575-609` [3](#0-2) .

### Citations

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

**File:** crypto/dkg/pedpop/src/lib.rs (L313-336)
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

    commitments.insert(self.params.i(), self.our_commitments.drain(..).collect());
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
