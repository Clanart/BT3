### Title
Panic (indexing absent `enc_keys` entry) on blame path in PedPoP decryption - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
The Zephyr Dhara bug is a NULL-pointer write on an error path: a callback unconditionally wrote the error code through a caller-supplied `err` pointer that upstream callers legitimately pass as `NULL` during journal resume. The Serai analog is an unchecked `HashMap` index on the PedPoP blame/error path: `Decryption::decrypt_with_proof` looks up `self.enc_keys[&decryptor]` with the indexing operator, which panics if `decryptor` is a `Participant` for whom no encryption key was ever registered. A malicious DKG participant can submit a blame/accusation naming an arbitrary `decryptor` index, turning the error-verification path into a crash of the honest party evaluating the blame proof.

### Finding Description
`EncryptedMessage` handling in PedPoP uses a per-participant encryption-key registry (`Decryption::enc_keys`). During blame resolution, `decrypt_with_proof` verifies the accuser-supplied `EncryptionKeyProof` (a DLEq proof) and, if valid, uses `proof.key` as the ECDH shared point to decrypt the accused message:

```rust
// crypto/dkg/pedpop/src/encryption.rs:381-393
if let Some(proof) = proof {
  proof
    .dleq
    .verify(
      &mut encryption_key_transcript(self.context),
      &[C::generator(), msg.key],
      &[self.enc_keys[&decryptor], *proof.key],   // <-- panics if decryptor not registered
    )
    .map_err(|_| DecryptionError::InvalidProof)?;
  cipher::<C>(self.context, &proof.key).apply_keystream(msg.msg.as_mut().as_mut());
  Ok(msg.msg)
}
```

`self.enc_keys[&decryptor]` uses `HashMap`'s `Index` impl, which panics on a missing key — the Rust analog of dereferencing a NULL `err` pointer on an error path. The `decryptor` value is attacker-influenced: it names whose encryption key the accused message was encrypted to, and blame accusations are submitted by other DKG participants. `register` only inserts keys for participants that actually sent a valid `EncryptionKeyMessage`; a participant who never registered (or an out-of-set index) leaves no entry. The DLEq verify is evaluated *before* any validity check on `decryptor`, so the panic fires before `InvalidProof` can be returned.

Like the Dhara case, the crash only manifests on the failure/blame path — normal operation never touches it — and the fix is the same shape: route the access through a fallible lookup (`self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?`) instead of an unconditional index.

Caveat: `decrypt_with_proof` is `pub(crate)`, invoked from PedPoP's blame-verification logic in `crypto/dkg/pedpop/src/lib.rs`; I did not fully trace every call site, so reachability depends on blame messages carrying a verifier-controlled `decryptor`/`from` pair, which the protocol design implies (the accuser states who the bad share was destined for).

### Impact Explanation
An unprivileged DKG participant can crash any honest validator that evaluates their blame proof by naming a `decryptor` with no registered encryption key. This is a denial of service against the DKG/key-rotation process — matching the CVE's kernel-fault DoS. If the crash aborts DKG completion, it can stall validator-set handover and signing availability.

### Likelihood Explanation
Triggering requires only sending a malformed blame message during a PedPoP DKG round — fully within the attacker's control as a protocol participant. No media wear or fault injection needed; unlike the NAND case, the missing-map-key condition is deterministic.

### Recommendation
Replace `self.enc_keys[&decryptor]` with a fallible lookup returning `DecryptionError::InvalidProof` when `decryptor` is unregistered, and validate `decryptor`/`from` against the expected participant set before indexing. Audit sibling indexing sites (`self.decryption.enc_keys[&participant]` at line 466, `view.verification_share(*l)` / `responses[l]` in `crypto/frost/src/sign.rs:477-479`) for the same pattern.

### Proof of Concept
1. In a PedPoP DKG round, attacker withholds their `EncryptionKeyMessage` (or targets a `Participant` index that never registered).
2. Attacker submits a blame accusation naming that `decryptor`, with an otherwise well-formed `EncryptedMessage` (valid PoP so the `msg.pop.verify` check at line 374 passes and a `proof` is supplied).
3. Honest party calls `decrypt_with_proof`; `self.enc_keys[&decryptor]` panics before the DLEq verification or `InvalidProof` return, aborting the process.