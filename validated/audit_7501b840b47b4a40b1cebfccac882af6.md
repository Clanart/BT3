### Title
Missing-key HashMap index panic in PedPoP encryption layer aborts node on unregistered participant - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::register`, `Decryption::decrypt_with_proof`, and `Encryption::encrypt` index `self.enc_keys[participant]` with `HashMap`'s `Index` impl, which panics when the key is absent. A participant who never delivers (or whose `EncryptionKeyMessage` is rejected before `register`) leaves no entry, so a subsequent `encrypt`/`decrypt_with_proof` call for that `Participant` panics — a crafted-input denial of service analogous to CVE-2019-7153's NULL-pointer dereference in `WasmBinaryBuilder::processFunctions`.

### Finding Description
`Decryption::register` only inserts into `enc_keys` when a valid `EncryptionKeyMessage` is supplied (`assert!` on duplicate aside) [1](#0-0) . Two call sites then dereference the map by `Index`:

- `decrypt_with_proof`: `&[self.enc_keys[&decryptor], *proof.key]` — panics if `decryptor` never registered an encryption key [2](#0-1) .
- `encrypt`: `self.decryption.enc_keys[&participant]` — panics if `participant` never registered [3](#0-2) .

`encrypt` is invoked while broadcasting per-participant shares in PedPoP, iterated over the session's participant set (all indexes `1..=n` via `ThresholdParams::all_participant_indexes`) [4](#0-3) , not over `enc_keys`' contents. `EncryptedMessage::read` and `EncryptionKeyMessage::read` accept untrusted peer bytes and produce `io::Result`, so a peer can simply withhold or corrupt their registration message [5](#0-4) [6](#0-5) .

### Impact Explanation
A panic unwinds (or aborts, under `panic=abort`) the task running the DKG — an unprivileged counterparty that withholds or malforms its `EncryptionKeyMessage` can crash any honest participant's PedPoP session, and can similarly crash blame resolution via `decrypt_with_proof`. This is a reachability-preserving analog of the Binaryen NULL-deref: an index dereference (`enc_keys[...]`) on a value the attacker caused to be absent.

### Likelihood Explanation
The panic requires that `encrypt`/`decrypt_with_proof` be invoked for a `Participant` whose key was never registered. If the caller's PedPoP driver pre-validates that all `1..=n` registrations arrived before encrypting, the path is unreachable; the crate does not itself enforce that invariant (its docs disclaim networking-level checks for related cases [7](#0-6) ). Exploitation also requires being a DKG participant, so this is a peer-reachable Medium, not remote-unauthenticated.

### Recommendation
Replace map indexing with `enc_keys.get(&participant).ok_or(...)` returning a typed error (e.g., a new `DkgError`/`DecryptionError` variant) in `decrypt_with_proof` and `encrypt`, and surface missing registrations as protocol errors instead of panics. The duplicate-registration `assert!` in `Decryption::register` (line 356-359) is likewise attacker-influenceable and should return an error.

### Proof of Concept
Conceptual: an honest PedPoP node with `ThresholdParams { t, n, i }` registers encryption keys as `EncryptionKeyMessage`s arrive. Participant `j` sends no (or an unparseable) `EncryptionKeyMessage`, so `enc_keys` has no entry for `j`. When the node reaches the share-distribution step and calls `self.encrypt(rng, j, share)` (or a blame flow calls `decrypt_with_proof(.., decryptor: j, ..)`), `self.decryption.enc_keys[&j]` panics with `HashMap` index-out-of-keys, crashing the session — mirroring the wasm-opt segfault class of the reference advisory.

Caveat: I was unable to fully trace the PedPoP driver code that iterates recipients for `encrypt`; if it iterates only `enc_keys`' present entries or errors on missing registrations first, this reduces to an internal-API panic rather than a peer-reachable DoS.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L56-59)
```rust
impl<C: Ciphersuite, M: Message> EncryptionKeyMessage<C, M> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L170-177)
```rust
impl<C: Ciphersuite, E: Encryptable> EncryptedMessage<C, E> {
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self {
      key: C::read_G(reader)?,
      pop: SchnorrSignature::<C>::read(reader)?,
      msg: Zeroizing::new(E::read(reader, params)?),
    })
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

**File:** crypto/dkg/src/lib.rs (L194-197)
```rust
  /// An iterator over all participant indexes.
  pub fn all_participant_indexes(&self) -> impl Iterator<Item = Participant> {
    AllParticipantIndexes { i: 1, n: self.n }
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L96-101)
```rust
/// The commitments message, intended to be broadcast to all other parties.
///
/// Every participant should only provide one set of commitments to all parties. If any
/// participant sends multiple sets of commitments, they are faulty and should be presumed
/// malicious. As this library does not handle networking, it is unable to detect if any
/// participant is so faulty. That responsibility lies with the caller.
```
