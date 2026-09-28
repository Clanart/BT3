### Title
`EncryptionKeyProof` `Debug` impl leaks the ECDH shared secret, enabling decryption of DKG secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptionKeyProof<C>` derives `fmt::Debug`, which prints the `key` field — a `Zeroizing<C::G>` holding the ECDH shared point between the sender's per-message encryption key and the recipient's long-term encryption key. `zeroize`'s `Zeroizing<T>` forwards `Debug` to the inner value, so `{:?}`/`{:#?}` formatting of an `EncryptionKeyProof` emits the raw shared secret. This is the direct analog of CVE-2024-3744 (secret material observable in logs/Debug output) mapped onto Serai's PedPoP encryption layer.

### Finding Description
The proof structure at `crypto/dkg/pedpop/src/encryption.rs:260-264` is declared:

```rust
#[derive(Clone, PartialEq, Eq, Debug, Zeroize)]
pub struct EncryptionKeyProof<C: Ciphersuite> {
  key: Zeroizing<C::G>,
  dleq: DLEqProof<C::G>,
}
```

`key` is populated in `Encryption::decrypt` with `ecdh::<C>(&self.enc_key, msg.key)` — the shared Diffie-Hellman point — and returned alongside the decrypted message (`encryption.rs:487-499`). The cipher for the message is derived solely from `context` (public) and this ECDH point via `cipher::<C>(context, &ecdh)` (`encryption.rs:101-133`, static IV `"DKG IV v0.2\0"`).

The codebase is otherwise careful to suppress secrets in `Debug`: `SecretShare` uses `finish_non_exhaustive` (`pedpop/src/lib.rs:242-246`), `SecretShareMachine`/`KeyMachine`/`Encryption` hand-write `Debug` to omit `coefficients`, `secret`, and `enc_key` (`encryption.rs:410-420`), and `ThresholdCore` omits `secret_share`. `EncryptionKeyProof` — which is `pub` and `Clone` — is the exception: it prints the one value that, combined with the already-public `EncryptedMessage` ciphertext bytes (`key` pubkey, `pop`, `msg`), fully determines the ChaCha20 keystream.

### Impact Explanation
Any `EncryptedMessage` (the encrypted secret share sent over the channel during PedPoP round 2) is public transcript data. If an `EncryptionKeyProof` for it is ever formatted — e.g., an integrator logs the proof produced during blame handling, or formats a containing error/struct — a party with log access can reconstruct `cipher::<C>(context, &proof.key)` and XOR-decrypt `msg.msg`, recovering the underlying `SecretShare` scalar in plaintext. That is threshold key-share recovery: a t-subset of leaked shares (or even a single share weakening the scheme) compromises the distributed key. This matches the advisory's class: secrets observable by an actor with log access, no protocol-level attack needed.

### Likelihood Explanation
Medium. Exploitation requires (a) the proof to reach a `Debug` sink — plausible since blame/decryption proofs are exactly the kind of object logged during DKG fault handling — and (b) access to logs plus the public `EncryptedMessage`. Both conditions mirror the CVE's preconditions (log access + `TokenRequests`/verbose flags). The leak is unconditional once formatted: `Zeroizing` provides no redaction.

### Recommendation
Replace `derive(Debug)` on `EncryptionKeyProof` with a manual `fmt::Debug` impl that omits `key` (e.g., `.field("dleq", &self.dleq).finish_non_exhaustive()`), consistent with the redacting impls used for `SecretShare`, `Encryption`, and `ThresholdCore`.

### Proof of Concept
```rust
// Given any EncryptionKeyProof (e.g., returned by Encryption::decrypt)
let proof: EncryptionKeyProof<C> = ...;
// This emits the raw ECDH shared point:
let leaked = format!("{proof:?}"); // contains `key` group encoding

// With the public EncryptedMessage `msg` and leaked point P:
let mut keystream = cipher::<C>(context, &Zeroizing::new(P)); // context is public
let mut plaintext = msg_serialized_bytes; // EncryptedMessage::serialize()
keystream.apply_keystream(&mut plaintext); // plaintext = SecretShare repr
let share = SecretShare::read(&mut plaintext.as_slice(), params).unwrap();
```
Root cause: `#[derive(Debug)]` at `crypto/dkg/pedpop/src/encryption.rs:260` on a struct containing `Zeroizing<C::G>` secret material; `Zeroizing<T: Debug>` transparently forwards `fmt::Debug`, defeating the file's otherwise uniform secret-redaction policy.