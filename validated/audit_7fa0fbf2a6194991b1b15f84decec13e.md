### Title
Missing-participant panic in PedPoP encryption key map: unregistered index lookups crash instead of erroring - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2018-5335 is a Wireshark dissector crash caused by failing to validate available buffer length before consuming attacker-controlled data. The analogous bug class in Serai is "index into participant-keyed state without validating the participant was registered, panicking rather than returning an error." In `crypto/dkg/pedpop/src/encryption.rs`, three lookups on `self.enc_keys` (a `HashMap<Participant, C::G>` populated only by `Decryption::register`) panic when the queried participant never submitted an `EncryptionKeyMessage`:

- `Decryption::register` panics via `assert!` if a participant's key is registered twice [1](#0-0) 
- `Encryption::encrypt` indexes `self.decryption.enc_keys[&participant]` unconditionally [2](#0-1) 
- `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` when checking a blame proof [3](#0-2) 

### Finding Description
`EncryptedMessage::read` / `EncryptionKeyMessage::read` consume untrusted DKG-round bytes [4](#0-3) . Registration into `enc_keys` only happens through `Decryption::register`, which is driven by whatever per-participant `EncryptionKeyMessage`s the caller collected [5](#0-4) . The PedPoP protocol assumes each participant broadcasts exactly one registration, but nothing in this library enforces that a key exists before it is used:

- A participant who withholds (or whose message is dropped for) their `EncryptionKeyMessage` causes `enc_keys[&participant]` in `Encryption::encrypt` to panic when honest parties encrypt shares to them — there is no `get`/`ok_or` validation.
- During blame resolution, `decrypt_with_proof` computes `self.enc_keys[&decryptor]`; a `decryptor` index that was never registered panics inside `HashMap` indexing.
- `register`'s `assert!` on duplicate registration turns any duplicated delivery of an `EncryptionKeyMessage` into a panic rather than a rejection.

This is the same root cause as the CVE: parsing/processing attacker-influenced protocol data where a precondition (buffer length there, key-map membership here) is assumed rather than checked, producing a crash.

### Impact Explanation
An abort panic in a participant's DKG process. Per run, a faulty or malicious participant can crash honest parties' PedPoP sessions, preventing key generation from completing (availability loss on the threshold-signing path). This maps to the CVSS 6.5 network-reachable availability impact of the reference advisory. Impact is limited to availability — no key material is leaked and no invalid share is accepted — consistent with a Medium rating.

### Likelihood Explanation
Triggering requires a PedPoP participant to cause a missing/duplicate `EncryptionKeyMessage` or for a blame flow to reference an unregistered `decryptor`. The protocol does not defend against a participant omitting their message before `encrypt` is invoked; whether a caller can avoid passing an incomplete message set is an integration detail the library does not enforce (it explicitly disclaims handling networking/message dedup). The panic site itself is deterministic and unconditional once the index is absent.

### Recommendation
Replace infallible map accesses with checked lookups:

- In `Encryption::encrypt`, use `self.decryption.enc_keys.get(&participant).copied().ok_or(...)` and surface a `DkgError`-style error instead of panicking.
- In `Decryption::decrypt_with_proof`, return `DecryptionError::InvalidProof` (or a new variant) when `enc_keys.get(&decryptor)` is `None`.
- In `Decryption::register`, return an error on duplicate registration rather than `assert!`, or silently ignore duplicates if the protocol intends last-write-wins/first-write-wins semantics — document whichever is chosen.

### Proof of Concept
Conceptual reproduction (requires the `pedpop` internals, e.g. via a `#[cfg(test)]` harness in the crate):

```rust
// crypto/dkg/pedpop/src/encryption.rs path
let mut enc: Encryption<C> = Encryption::new(context, Participant::new(1).unwrap(), &mut rng);
// Register enc keys for participants 2..=n-1, but never for participant n
for l in 2 .. n {
  enc.register(Participant::new(l).unwrap(), their_key_message);
}
// Encrypting a share to participant n panics on `enc_keys[&participant]`
enc.encrypt(&mut rng, Participant::new(n).unwrap(), share_msg); // panic: index not found
```

Similarly, `Decryption::new(context).decrypt_with_proof::<E>(from, unregistered_decryptor, msg_with_valid_pop, Some(proof))` panics at `self.enc_keys[&decryptor]` once `msg.pop` verifies — an attacker can satisfy the PoP check by signing under their own `msg.key`, so the panic is reachable before the DLEq check is ever evaluated.

Caveat: I could not fully trace the PedPoP caller code (`crypto/dkg/pedpop/src/lib.rs` beyond the `Commitments`/`challenge` portions) to confirm whether the library itself invokes `encrypt`/`decrypt_with_proof` over participant sets the attacker can partially control, or whether it pre-validates completeness. The panic sites are unconditional; the reachability hinges on an attacker being able to cause an absent or duplicated registration, which the library's own docs acknowledge is possible ("this library does not handle networking").

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L171-177)
```rust
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

**File:** crypto/dkg/pedpop/src/encryption.rs (L452-458)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    self.decryption.register(participant, msg)
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
