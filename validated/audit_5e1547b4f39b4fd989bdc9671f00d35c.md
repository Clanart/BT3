### Title
Unchecked map access in PedPoP blame decryption lets a crafted accusation panic the node - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP DKG encryption layer resolves blame by looking up a participant's registered encryption key in a `HashMap` via direct indexing (`enc_keys[&decryptor]`) without checking presence. The `decryptor` participant index originates from the blame/accusation flow and is attacker-influenced. If the accused/decryptor participant never successfully registered an encryption key (or the accusation names an index with no registered key), the lookup panics, crashing the node — a direct analog of the CVE-2018-15861 pattern (missing NULL/failure check on attacker-influenced state causing a crash).

### Finding Description
`Decryption::decrypt_with_proof` verifies the accused message's PoP and DLEq proof, then performs the ECDH key lookup:

```rust
// crypto/dkg/pedpop/src/encryption.rs:381-392
if let Some(proof) = proof {
  proof
    .dleq
    .verify(
      &mut encryption_key_transcript(self.context),
      &[C::generator(), msg.key],
      &[self.enc_keys[&decryptor], *proof.key],   // line 388: unchecked indexing
    )
    .map_err(|_| DecryptionError::InvalidProof)?;
  cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
```

`self.enc_keys` is only populated by `Decryption::register` (lines 351-361), which inserts a key per participant when their `EncryptionKeyMessage` is processed. There is no guarantee every `Participant` value in `1..=n` has a registered key at blame time — e.g., a participant who sent a malformed/absent registration, or a `decryptor` index supplied by the accuser that never registered. The same unchecked pattern exists at `Encryption::encrypt` (`self.decryption.enc_keys[&participant]`, line 466), which panics if asked to encrypt for an unregistered participant.

The sibling check exists elsewhere in the codebase (`ThresholdKeys::view` validates all `included` indexes and returns `DkgError` rather than panicking, crypto/dkg/src/lib.rs:463-491), showing the intended pattern is error propagation, not panic.

### Impact Explanation
A panic here aborts the DKG/key-rotation process (or the whole node task). Since the triggering input is a blame-flow message naming a `decryptor` participant with no registered encryption key, a single participant message can crash every honest node that evaluates the accusation — a liveness DoS on the threshold key generation/resharing path, matching the Medium-severity availability impact of the reference CVE.

### Likelihood Explanation
The crash requires only that a `decrypt_with_proof` call reference an absent key. Accusations are a normal protocol path, and a participant can deliberately withhold/malform their `EncryptionKeyMessage` registration, or the accuser's supplied `decryptor` index can point at any `Participant` value — there is no `contains_key` guard before indexing. This is reachable with ordinary protocol messages, not leaked keys or collusion.

### Recommendation
Replace `self.enc_keys[&decryptor]` (and `self.decryption.enc_keys[&participant]` in `encrypt`) with a fallible lookup:

```rust
let Some(enc_key) = self.enc_keys.get(&decryptor) else {
  return Err(DecryptionError::InvalidProof);
};
```

and map the missing key to a `DecryptionError`/`FrostError` variant so blame resolution degrades to "invalid accusation" instead of aborting.

### Proof of Concept
1. Initialize PedPoP for params where some participant `d` never calls `register` (or sends a registration message that fails to parse, so no key is inserted into `enc_keys`).
2. Submit an accusation against a sender whose `EncryptedMessage` carries a valid PoP (so the `InvalidSignature` early-return at line 374-379 is bypassed), naming `d` as `decryptor` and supplying a well-formed `EncryptionKeyProof`.
3. `decrypt_with_proof` reaches line 388, indexes `self.enc_keys[&d]` on the missing entry, and panics (`HashMap` index → `panic!`), aborting execution — analogous to the unhandled `xkb_intern_atom` NULL dereference: a missing presence check on an attacker-influenced key crashes the process.

Uncertainty note: exact call-site wiring of `decrypt_with_proof` into the surrounding coordinator blame flow was not fully traced (it is `pub(crate)`), so reachability depends on callers forwarding accuser-chosen `decryptor` values; the panic itself on a missing `enc_keys` entry is unconditional.