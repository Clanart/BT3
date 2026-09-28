### Title
Panic on duplicate/out-of-order DKG encryption-key registration causes participant DoS - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP DKG's `Decryption::register` uses an `assert!` to reject a second `EncryptionKeyMessage` for an already-registered `Participant`, and `decrypt_with_proof`/`encrypt` index `self.enc_keys` with the HashMap `[]` operator, which panics on a missing key. A DKG participant who submits a duplicate encryption-key registration (or whose key is referenced before registration) crashes the processing node instead of producing a `Fault`/`io::Error`, mirroring CVE-2022-31009's "assert on attacker-controlled value instead of fallback" bug class. [1](#0-0) [2](#0-1) 

### Finding Description
`Decryption::register` is invoked once per peer-supplied `EncryptionKeyMessage<C, M>` during the PedPoP commitment/registration phase. The message itself is fully attacker-controlled bytes deserialized via `EncryptionKeyMessage::read` (`M::read` + `C::read_G`), and the only validation before registration is this assertion: [3](#0-2) 

```rust
// crypto/dkg/pedpop/src/encryption.rs
assert!(
  !self.enc_keys.contains_key(&participant),
  "Re-registering encryption key for a participant"
);
self.enc_keys.insert(participant, msg.enc_key);
``` [4](#0-3) 

A peer who sends two registration messages (or whose message is replayed by an unauthenticated channel — the file itself notes the DKG does not offer an authenticated channel, `pop_challenge` commentary at lines 299–301) triggers a panic that unwinds/aborts the entire participant process rather than returning a blameable fault.

Two sibling sites have the same class: `encrypt` uses `self.decryption.enc_keys[&participant]` (line 466) and `decrypt_with_proof` uses `self.enc_keys[&decryptor]` (line 388); both panic via HashMap indexing if the referenced participant never registered — a state reachable when messages are processed in attacker-influenced order, since `EncryptedMessage::read`/`EncryptionKeyProof::read` accept arbitrary participant-tagged bytes. [5](#0-4) [6](#0-5) 

The contrast with the rest of the crate is direct: every other deserialization path (`Commitments::read`, `EncryptedMessage::read`, `C::read_F`/`C::read_G`) returns `io::Result` and degrades gracefully on malformed input; these three sites are the only input-dependent panics on the DKG message path. [7](#0-6) [8](#0-7) 

### Impact Explanation
Availability (Medium, matching the CVE's A:H profile scaled to a multi-party protocol). A single malicious or Byzantine DKG participant can crash every honest peer processing its messages, aborting the key-generation ceremony. Repeated across DKG attempts, this permanently prevents a multisig/validator set from completing PedPoP — the same "crash on launch/processing, no workaround, repeated" shape as the Wire accent-color bug. Because the panic happens inside library code with no `io::Error` path, an integrator cannot gracefully degrade; the process must be restarted and the offender filtered out-of-band.

### Likelihood Explanation
High within the threat model: the DKG explicitly assumes a broadcast channel where "if any participant sends multiple sets of commitments, they are faulty" (`Commitments` doc, crypto/dkg/pedpop/src/lib.rs:96-101) — i.e., duplicate/malformed per-participant messages are an acknowledged, expected input class, not an integrator MUST-misuse. An unregistered `decryptor` index is reachable whenever a blame/accusation message names a participant that hasn't (or can't) register an encryption key.

### Recommendation
Replace `assert!` in `Decryption::register` with a fallible check returning `Err`/`DecryptionError` (or a `dkg::Fault`-style blameable error) on duplicate registration; replace `enc_keys[&participant]`/`enc_keys[&decryptor]` indexing with `.get(..).ok_or(..)` so an unregistered peer yields an error instead of a panic.

### Proof of Concept
```rust
// crypto/dkg/pedpop — conceptual PoC over public API surface
// Attacker is DKG participant `l`. Honest node runs the PedPoP registration flow.

// Round 1: attacker sends a valid EncryptionKeyMessage for participant l.
let msg1 = EncryptionKeyMessage::<C, M>::read(&mut bytes1, params)?;
decryption.register(l, msg1); // enc_keys[l] = enc_key

// Round 2 (or replay on the unauthenticated broadcast channel):
// attacker sends a second registration for the same participant l.
let msg2 = EncryptionKeyMessage::<C, M>::read(&mut bytes2, params)?;
decryption.register(l, msg2);
// ^ panics: "Re-registering encryption key for a participant"
//   crypto/dkg/pedpop/src/encryption.rs:356

// Variant B — missing-key index panic:
// A blame/accusation flow calls decrypt_with_proof with `decryptor = m`
// where m never registered an encryption key:
decryption.decrypt_with_proof(from, m, msg, Some(proof));
// ^ panics at self.enc_keys[&decryptor] (encryption.rs:388)
```

Caveat: `register`/`decrypt_with_proof` are `pub(crate)`, so exact reachability depends on the in-crate call path in `crypto/dkg/pedpop/src/lib.rs` (the `verify_commitments`/share flow); the crate's own docs confirm duplicate per-participant messages are an attacker-controllable input, making the panic reachable rather than an internal invariant.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

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

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-397)
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
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L460-467)
```rust
  pub(crate) fn encrypt<R: RngCore + CryptoRng, E: Encryptable>(
    &self,
    rng: &mut R,
    participant: Participant,
    msg: Zeroizing<E>,
  ) -> EncryptedMessage<C, E> {
    encrypt(rng, self.context, self.i, self.decryption.enc_keys[&participant], msg)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L109-128)
```rust
impl<C: Ciphersuite> ReadWrite for Commitments<C> {
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
  }
```
