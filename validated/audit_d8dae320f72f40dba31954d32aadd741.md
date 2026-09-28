### Title
Unprivileged DKG participant crashes honest nodes via unchecked `enc_keys` HashMap indexing during PedPoP decryption — (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Java serialization CVE-2022-21341 is an unauthenticated, network-reachable partial denial of service triggered by feeding crafted input into a deserialization/protocol path. The direct analog in Serai is in PedPoP's encryption layer: `Decryption::decrypt_with_proof` and `Encryption::encrypt`/`decrypt` index `self.enc_keys[...]` / `self.decryption.enc_keys[...]` with `HashMap` square-bracket indexing, which panics when the key is absent. A participant who submits an `EncryptedMessage` (secret share) without first submitting a valid `EncryptionKeyMessage` — or who is named as `decryptor` in a blame flow without having registered — causes an honest node to panic on attacker-supplied message content.

### Finding Description
`EncryptedMessage::read` accepts untrusted bytes over the wire (key point, PoP signature, ciphertext) and returns them to the DKG driver without error for arbitrary sender `Participant` indexes (crypto/dkg/pedpop/src/encryption.rs:171-177). The subsequent processing paths index a `HashMap<Participant, C::G>` of registered encryption keys with `[]` instead of `get()`:

- `decrypt_with_proof`: `&[self.enc_keys[&decryptor], *proof.key]` — panics if `decryptor` never registered an encryption key (line 388).
- `Encryption::encrypt`: `self.decryption.enc_keys[&participant]` — panics if the target participant never registered (line 466).

The only guard on registration is `Decryption::register`'s `assert!(!self.enc_keys.contains_key(&participant))` (lines 356-358), which is itself a panic reachable if the DKG driver ever calls `register` twice for the same participant — i.e., panic is the established failure mode of this module, not a `Result` error.

`EncryptedMessage::read` and `EncryptionKeyMessage::read` are exactly the untrusted-bytes entry points the scan rules designate as reachable to an unprivileged party. Nothing in `read` ties the message's `from` participant to a previously registered encryption key, so the absence check is entirely on the consumer.

### Impact Explanation
A single malicious DKG participant (or anyone able to inject a message attributed to a participant index that skipped registration) causes a panic (`HashMap` index out of bounds → `panic!`) in every honest node that attempts to encrypt to or decrypt from that index. This aborts the key-generation session — a partial denial of service matching the CVE's availability-only impact (CVSS A:L, no confidentiality/integrity impact). No secret material is required to trigger it; only public protocol messages.

### Likelihood Explanation
Reachable by any participant in a PedPoP session: send an `EncryptedMessage` share while withholding your `EncryptionKeyMessage`, or omit registration and let the coordinator's encrypt-to-all step hit `enc_keys[&participant]`. Triggering requires only malformed protocol participation, not collusion, key knowledge, or integrator misuse — the `read` functions impose no precondition. Likelihood is bounded by the fact that a crash only kills the local session/node rather than corrupting state, and BFT relaunch may partially mitigate — consistent with Medium, mirroring the source CVE's rating.

### Recommendation
Replace all `HashMap` indexing on `enc_keys` with checked lookups:

- In `decrypt_with_proof` (line 388): use `self.enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)?`.
- In `Encryption::encrypt` (line 466): return a `Result`/`Option` or pre-validate that all `n` participants registered before encryption begins.
- In `Decryption::register` (line 357): return an error instead of `assert!` so a duplicate `EncryptionKeyMessage` is a rejectable message, not a crash.

### Proof of Concept
Conceptual (requires a running PedPoP session over `Ristretto`):

1. Honest node runs `Encryption::<Ristretto>::new(context, Participant(2), rng)` and broadcasts its `EncryptionKeyMessage` via `registration()`.
2. Malicious `Participant(1)` never sends an `EncryptionKeyMessage`, so `register(Participant(1), ...)` is never called and `enc_keys` has no entry for `Participant(1)`.
3. `Participant(1)` does send a structurally valid `EncryptedMessage` (any ciphertext; `EncryptedMessage::read` succeeds since `read_G`/`SchnorrSignature::read` only check encodings).
4. When the honest node reaches the encrypt phase, `self.encrypt(rng, Participant(1), msg)` executes `self.decryption.enc_keys[&Participant(1)]` → panic: `index out of bounds`/missing key. Equivalently, in the blame flow, calling `decrypt_with_proof` with `decryptor = Participant(1)` panics at line 388.

Net effect: key generation aborts on all honest nodes that process the message — an unauthenticated availability fault directly analogous to CVE-2022-21341's serialization-driven partial DoS.

Caveat: I could not confirm the exact PedPoP driver code (`crypto/dkg/pedpop/src/lib.rs`) that maps message senders to `encrypt`/`decrypt` calls; the panic sites themselves are verified above, but whether the driver pre-filters senders to registered participants determines reachability.