### Title
Out-of-range `Participant` in `BlameMachine::blame`/`AdditionalBlameMachine::blame` causes a panic (denial of service) - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
Analogous to the TensorFlow `CHECK`-fail-on-bad-input-shape bug class, PedPoP's blame-evaluation path indexes internal maps with caller-supplied `Participant` values that are never bounds-checked against `1 ..= n`. An unprivileged party can submit a blame/accusation naming a `sender` or `recipient` outside the DKG participant set, hitting `self.enc_keys[&decryptor]` / `self.commitments[&sender]` and panicking instead of returning an error, aborting the host process.

### Finding Description
`BlameMachine::blame` and `AdditionalBlameMachine::blame` are public APIs explicitly designed to evaluate accusations raised by other (untrusted) DKG participants. All four inputs (`sender`, `recipient`, `msg`, `proof`) originate from the accusing party. `blame_internal` passes `recipient` into `Decryption::decrypt_with_proof`, which performs `self.enc_keys[&decryptor]` — a `HashMap` indexing operation that panics if `recipient` was never registered. `enc_keys` only ever contains indexes `1 ..= n` (populated via `Encryption::register` in `verify_r1` or `AdditionalBlameMachine::new`), so any `recipient` with `u16::from(recipient) > n` panics immediately. [1](#0-0) 

Even if `recipient` is in-range, the attacker can satisfy both checks in `decrypt_with_proof` themselves: they can generate a fresh scalar `k`, set `msg.key = k*G`, produce a valid PoP signature over the attacker-known challenge, and set `proof.key = k*enc_key_recipient` with a valid `DLEqProof` (they know `k`). This requires no secret knowledge — the DLEq is over the recipient's *public* encryption key. `decrypt_with_proof` then returns `Ok`, and `blame_internal` proceeds to `self.commitments[&sender]`, which panics for any `sender > n`. [2](#0-1) 

Unlike the duplicate-commitments case, which the docs flag as caller responsibility, nothing documents that `sender`/`recipient` must be validated by the caller before calling `blame`, and `Participant::new` accepts any nonzero `u16`, including values larger than `n`. [3](#0-2) 

### Impact Explanation
A single crafted blame request panics the host process (Rust `HashMap` index panic), killing the DKG participant node mid-protocol — a direct denial of service mirroring the reference advisory's `CHECK`-fail DoS. If the blame handler runs on an arbitrator/`AdditionalBlameMachine` shared by validators, one malicious accusation takes it down.

### Likelihood Explanation
Blame evaluation exists precisely to consume unauthenticated accusations from arbitrary parties; feeding an out-of-range `sender`/`recipient` requires no cryptographic break, only a valid self-constructed `EncryptedMessage` + `EncryptionKeyProof`, which any party can build offline. Confidence is limited by the fact that reachability depends on the integrator routing peer-supplied accusation fields into `blame` — which is the API's intended purpose — and by whether the caller validates participant indexes itself (nothing in the API forces this).

### Recommendation
In `blame_internal` (and `Decryption::decrypt_with_proof`), replace `self.enc_keys[&decryptor]` and `self.commitments[&sender]` with `get()`/`ok_or`-style lookups returning `PedPoPError::InvalidParticipant`/`MissingParticipant`, and reject `sender`/`recipient` not in `1 ..= n` up front. Generally, avoid `map[key]` indexing on attacker-influenced keys throughout `crypto/dkg`.

### Proof of Concept
```rust
// n = 3 DKG completed; attacker submits an accusation naming a nonexistent sender.
// additional_blame_machine was built via AdditionalBlameMachine::new(context, 3, msgs)
let fake_sender = Participant::new(42).unwrap(); // > n
let recipient   = Participant::new(1).unwrap();  // registered in enc_keys

// Attacker constructs a fully valid EncryptedMessage + EncryptionKeyProof:
let k = Zeroizing::new(Ed25519::random_nonzero_F(rng));
let key = Ed25519::generator() * k.deref();
// msg.msg = any 32 bytes; pop = SchnorrSignature::sign(&k, nonce, pop_challenge(...))
// proof.key = k * enc_key_of(recipient); proof.dleq = DLEqProof::prove(..., &k)
// decrypt_with_proof returns Ok(gibberish share bytes)
// -> blame_internal reaches self.commitments[&fake_sender] -> PANIC (index out of map)
let blamed = additional_blame_machine.blame(fake_sender, recipient, msg, Some(proof));
```

If `recipient` itself is chosen `> n`, the panic occurs even earlier at `self.enc_keys[&decryptor]` inside `decrypt_with_proof`, requiring no valid proof at all.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L385-390)
```rust
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
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

**File:** crypto/dkg/src/lib.rs (L29-36)
```rust
  pub const fn new(i: u16) -> Option<Participant> {
    if i == 0 {
      None
    } else {
      Some(Participant(i))
    }
  }

```
