### Title
PedPoP `calculate_share` returns a blame proof revealing the ECDH shared key before the message's proof-of-possession is verified - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
In PedPoP's `KeyMachine::calculate_share`, the PoP `SchnorrSignature` on each `EncryptedMessage` is only *queued* into a `BatchVerifier` (executed later), yet an invalidly serialized share causes an early `Err` return that already carries an `EncryptionKeyProof` containing `key = b * msg.key`. An attacker can replay a victim's `msg.key` to make the victim emit a proof that decrypts the victim's real encrypted share.

### Finding Description
`Encryption::decrypt` in `crypto/dkg/pedpop/src/encryption.rs` does two things: it queues the per-message proof-of-possession (`msg.pop`) for later batch verification, and it unconditionally computes `key = ecdh(&self.enc_key, msg.key)` (i.e. `b * msg.key`) and packages it into an `EncryptionKeyProof` returned to the caller. [1](#0-0) 

The comment on `EncryptedMessage` documents exactly why the PoP must gate this: without it, "Bob would then use this to create a blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob." [2](#0-1) 

In `KeyMachine::calculate_share`, however, the decrypted bytes are run through `C::F::from_repr`, and a non-canonical scalar encoding produces an *immediate* error return that clones and attaches the blame proof — before `batch.verify_with_vartime_blame()` at line 493 ever executes the queued PoP check:

```rust
let (mut share_bytes, blame) =
  self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
let share =
  Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
    PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
  })?);
``` [3](#0-2) 

This is the same class of bug as the kernel report: a teardown/exposure action valid only in the "fully established" path (PoP verified) is performed on an object that reached it through a path where that precondition was never checked — the PoP is "detached" (queued but unverified) when the blame proof is already emitted.

### Impact Explanation
Eve (an unprivileged DKG participant) sends Bob an `EncryptedMessage` whose `key` field equals `X`, the public per-message key Alice used in her encrypted share to Bob (observable on the wire), with arbitrary ciphertext bytes that decode to a non-canonical scalar (e.g. an all-`0xFF` `SecretShare` repr; the test helper `invalidate_share_serialization` confirms such bytes exist). Eve's PoP cannot verify (she doesn't know `x` such that `X = xG`), but the PoP check is never reached: `from_repr` fails first and Bob returns `InvalidShare { blame: Some(EncryptionKeyProof { key: bX, dleq }) }`. `bX` is precisely the ChaCha20 shared key for Alice's message, so revealing it decrypts Alice's secret share sent to Bob — a secret share disclosure that the PoP design explicitly claims to prevent. The returned `Decryption` object can also be used via `decrypt_with_proof`/`BlameMachine` flows.

### Likelihood Explanation
Fully attacker-controlled: any DKG participant who observes another participant's `EncryptedMessage` (same authenticated-channel delivery) can read `msg.key` via `EncryptedMessage::read` and craft the triggering message with public inputs only. No collusion, threshold compromise, or key material is needed. Deterministic trigger — no probabilistic element.

### Recommendation
Verify (or reject) the PoP *before* producing or attaching the blame proof. Concretely, in `calculate_share` either (a) verify `msg.pop` eagerly inside `Encryption::decrypt` before computing `ecdh`, returning `BatchId::Decryption`-style failure with `blame: None` when it fails, or (b) defer constructing `EncryptionKeyProof`/the `from_repr` error path until after `batch.verify_with_vartime_blame()` confirms the PoP statement passed — i.e., on the `Decryption` failure id emit `blame: None` and never the key. Never return a blame proof exposing `b * msg.key` for a message whose PoP has not been verified.

### Proof of Concept
1. Alice (`i=2`) runs PedPoP with Bob (`i=1`) and Eve (`i=3`). Alice's `generate_secret_shares` emits `EncryptedMessage { key: X, pop, msg }` to Bob.
2. Eve observes the message bytes, extracts `X` via `EncryptedMessage::read`.
3. Eve builds `EncryptedMessage { key: X, pop: <any signature>, msg: SecretShare([0xFF; 32]) }` and sends it to Bob as her share.
4. Bob calls `KeyMachine::calculate_share(shares)`. For Eve's entry, `decrypt` queues the (invalid) PoP into `batch` and returns `blame = EncryptionKeyProof { key: bX, dleq }`.
5. `C::F::from_repr([0xFF; 32])` returns `None` → `Err(InvalidShare { participant: 3, blame: Some(blame) })` — returned before line 493's batch verification runs the PoP statement.
6. Bob surfaces the blame proof to Eve (the documented blame protocol). Eve decrypts Alice's real share message with `bX` via `cipher::<C>(context, &bX)`, recovering the contribution to Bob's private key share — key-share recovery by an unprivileged party.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L83-91)
```rust
  // Also include a proof-of-possession for the key.
  // If this proof-of-possession wasn't here, Eve could observe Alice encrypt to Bob with key X,
  // then send Bob a message also claiming to use X.
  // While Eve's message would fail to meaningfully decrypt, Bob would then use this to create a
  // blame argument against Eve. When they do, they'd reveal bX, revealing Alice's message to Bob.
  // This is a massive side effect which could break some protocols, in the worst case.
  // While Eve can still reuse their own keys, causing Bob to leak all messages by revealing for
  // any single one, that's effectively Eve revealing themselves, and not considered relevant.
  pop: SchnorrSignature<C>,
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L479-500)
```rust
    msg.pop.batch_verify(
      rng,
      batch,
      batch_id,
      msg.key,
      pop_challenge::<C>(self.context, msg.pop.R, msg.key, from, msg.msg.deref().as_ref()),
    );

    let key = ecdh::<C>(&self.enc_key, msg.key);
    cipher::<C>(self.context, &key).apply_keystream(msg.msg.as_mut().as_mut());
    (
      msg.msg,
      EncryptionKeyProof {
        key,
        dleq: DLEqProof::prove(
          rng,
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &self.enc_key,
        ),
      },
    )
```

**File:** crypto/dkg/pedpop/src/lib.rs (L477-483)
```rust
      let (mut share_bytes, blame) =
        self.encryption.decrypt(rng, &mut batch, BatchId::Decryption(l), l, share_bytes);
      let share =
        Zeroizing::new(Option::<C::F>::from(C::F::from_repr(share_bytes.0)).ok_or_else(|| {
          PedPoPError::InvalidShare { participant: l, blame: Some(blame.clone()) }
        })?);
      share_bytes.zeroize();
```
