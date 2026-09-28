### Title
Attacker-controlled DKG messages trigger panics in PedPoP encryption-key handling, crashing the participant - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
CVE-2017-9300 is a heap-corruption/crash via crafted bytes fed to a decoder. The Serai analog is reachable `panic!`/`assert!`/unchecked-`HashMap`-indexing in the PedPoP encryption layer, where the inputs are messages supplied by other (untrusted) DKG participants. A malicious or buggy DKG participant can abort an honest party's DKG/signing process — a denial of service caused purely by attacker-controlled message flow.

### Finding Description
Three reachable panic sites exist on paths driven by peer-supplied messages:

1. `Decryption::register` asserts that a participant's encryption key is not already present: `assert!(!self.enc_keys.contains_key(&participant), "Re-registering encryption key for a participant")`. A peer that sends a second `EncryptionKeyMessage` (or whose message is delivered twice by the transport) causes this assert to fire, panicking the honest node. `EncryptionKeyMessage` is parsed from untrusted bytes via `EncryptionKeyMessage::read` → `M::read` + `C::read_G`, and `Encryption::register` forwards directly into `Decryption::register` with no deduplication or error path. [1](#0-0) [2](#0-1) 

2. `Encryption::encrypt` indexes `self.decryption.enc_keys[&participant]` directly. If a peer withholds their `EncryptionKeyMessage` but the local protocol still attempts to encrypt shares for them, this indexing panics instead of returning an error. [3](#0-2) 

3. `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` when verifying an `EncryptionKeyProof`'s DLEq. If the decryptor's own key was never registered into the `Decryption` box, the blame/verification path panics on an attacker-triggered accusation flow. [4](#0-3) 

This is consistent with the crate's own threat commentary — it acknowledges unauthenticated networking and fault handling — yet uses `assert!`/indexing on peer-controlled state instead of returning `Err`.

### Impact Explanation
Analogous to CVE-2017-9300 (crafted input → application crash), a single faulty DKG participant can crash an honest participant's process during key generation by sending a duplicate `EncryptionKeyMessage`, or by withholding their registration while other phases proceed. This halts DKG execution and, in a validator/coordinator context, can be used to stall threshold key setup or signing sessions at negligible cost — the messages are small, well-formed group elements that pass `read_G`/`DLEqProof::read`/`SchnorrSignature::read` deserialization cleanly.

### Likelihood Explanation
The trigger requires only that the attacker be a DKG participant able to send a message twice or omit a registration — no threshold collusion, no secret material, no malformed encodings needed. The panic sites are unconditional once the state condition is met. The main caveat is reachability depends on the integration not deduplicating messages before calling `register`; the crate explicitly delegates networking/dedup responsibility to the caller (`Commitments` docs note the library "does not handle networking"), so integrations that pass through message streams as-received are exposed.

### Recommendation
Replace the `assert!` in `Decryption::register` and the `HashMap` indexing in `encrypt`/`decrypt_with_proof` with checked lookups returning `Result`/`DecryptionError` (e.g., `DuplicateKey`, `MissingEncryptionKey`). Peer-driven control flow should never abort the process; faults should be surfaced as blameable errors consistent with the existing `DecryptionError` enum.

### Proof of Concept
1. Instantiate `Encryption::<C>::new(context, our_i, rng)`.
2. Have an attacker send `EncryptionKeyMessage` for participant `l` twice.
3. Call `encryption.register(l, msg1)` then `encryption.register(l, msg2)` — the second call hits `assert!(!self.enc_keys.contains_key(&participant))` and panics.

Alternatively: attacker never registers; honest party proceeds to `encrypt(l, share)` → `self.decryption.enc_keys[&participant]` panics on missing key.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
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
