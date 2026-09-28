### Title
ECDH shared-secret decryption key exposed in plaintext via `EncryptionKeyProof`'s derived `Debug` impl - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to CVE-2019-1003021 (a Jenkins plugin rendering a client secret in plaintext to anyone who can view the form output), `EncryptionKeyProof` in `crypto/dkg/pedpop/src/encryption.rs` derives `Debug`, causing the ECDH shared point `key` — the sole secret protecting a DKG secret share's ChaCha20 ciphertext — to be rendered in plaintext whenever the proof is debug-formatted or logged.

### Finding Description
`EncryptionKeyProof` is defined at `crypto/dkg/pedpop/src/encryption.rs:260-264` as:

```rust
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyProof<C: Ciphersuite> {
  key: Zeroizing<C::G>,
  dleq: DLEqProof<C::G>,
}
```

`key` is `ecdh::<C>(&self.enc_key, msg.key)` computed in `Encryption::decrypt` (encryption.rs:487-492). It is the shared Diffie-Hellman point from which `cipher()` derives the ChaCha20 keystream that decrypts `msg.msg` — the secret share bytes (`share_verification_statements`/`polynomial` output). Whoever holds `key` can run `cipher::<C>(context, &key).apply_keystream(msg.msg.as_mut().as_mut())` exactly as `decrypt_with_proof` does at encryption.rs:392, and recover the plaintext secret share.

The crate otherwise treats secret material carefully: `SecretShare` has a manual non-exhaustive `Debug` (lib.rs:242-245), `Encryption` has a manual `Debug` that deliberately omits `enc_key` (encryption.rs:410-419), and `KeyMachine`/`SecretShareMachine`/`BlameMachine` all use `finish_non_exhaustive()` to withhold secrets (lib.rs:285-294, 395-403, 542-549). `EncryptionKeyProof` breaks this pattern: its `#[derive(Debug)]` prints `key` and `dleq` fields verbatim, since `Zeroizing<C::G>` forwards `Debug` to the inner point.

The proof is legitimately serialized over the wire during blame (processor/src/key_gen.rs:424), but `Debug` creates a second, uncontrolled disclosure channel: any `{:?}` formatting, `dbg!`, tracing/log capture, panic message, or error-chain formatting of a `PedPoPError::InvalidShare { blame: Some(proof) }` writes the share-decryption secret into logs or terminal output in plaintext — precisely the "secret visible to anyone who can view the output" class of the Jenkins advisory.

### Impact Explanation
A party able to read log output, crash reports, or any debug-formatted representation of the proof obtains `key` and can decrypt the corresponding `EncryptedMessage<C, SecretShare<C::F>>` ciphertext (which travels over authenticated but not necessarily confidential channels to the blame consumers) to recover a valid threshold secret share. Combined with `t - 1` other shares observed the same way, this enables full group-key share recovery. This matches the advisory's CWE-200 exposure-of-sensitive-information class, rated Medium.

### Likelihood Explanation
`EncryptionKeyProof` is embedded in `PedPoPError::InvalidShare { participant, blame }` returned from `SecretShareMachine::calculate_share`, and the processor serializes it into `ProcessorMessage::InvalidShare` (processor/src/key_gen.rs:419-425). Any downstream code that debug-formats the error or proof — a common pattern during fault handling — emits the ECDH secret. Reachability requires only that an implementation log or display the proof during a blame event, which is the exact moment the object is produced.

### Recommendation
Replace the derived `Debug` on `EncryptionKeyProof` with a manual implementation that withholds `key`, mirroring `Encryption`'s impl:

```rust
impl<C: Ciphersuite> fmt::Debug for EncryptionKeyProof<C> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt.debug_struct("EncryptionKeyProof")
      .field("dleq", &self.dleq)
      .finish_non_exhaustive()
  }
}
```

Audit other secret-bearing types (`ThresholdKeys`, `IndividualNonces`, FROST sign-machine states) for derived `Debug`/`Display` impls that emit scalar or ECDH material.

### Proof of Concept
```rust
// In a test or any code path handling a blame proof:
let proof: EncryptionKeyProof<Ristretto> = /* from decrypt() or read() */;
let leaked = format!("{proof:?}");            // derived Debug prints `key` point bytes
// Given `key` and the intercepted ciphertext `msg`:
let mut cipher = cipher::<Ristretto>(context, &proof.key); // same as decrypt_with_proof:392
cipher.apply_keystream(msg.msg.as_mut().as_mut());
// msg.msg now contains the plaintext SecretShare scalar bytes
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L259-264)
```rust
/// A proof that the provided encryption key is a legitimately derived shared key for some message.
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyProof<C: Ciphersuite> {
  key: Zeroizing<C::G>,
  dleq: DLEqProof<C::G>,
}
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-393)
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
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L487-500)
```rust
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

**File:** crypto/dkg/pedpop/src/lib.rs (L242-245)
```rust
impl<F: PrimeField> fmt::Debug for SecretShare<F> {
  fn fmt(&self, fmt: &mut fmt::Formatter<'_>) -> fmt::Result {
    fmt.debug_struct("SecretShare").finish_non_exhaustive()
  }
```

**File:** processor/src/key_gen.rs (L419-425)
```rust
                PedPoPError::InvalidShare { participant, blame } => {
                  Err(ProcessorMessage::InvalidShare {
                    id,
                    accuser: params.i(),
                    faulty: participant,
                    blame: Some(blame.map(|blame| blame.serialize())).flatten(),
                  })?
```
