### Title
Registered PedPoP encryption keys are accepted without proof of possession or identity rejection, making all shares sent to a malicious participant decryptable by passive observers - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptionKeyMessage` carries a freely chosen group element `enc_key` which `Decryption::register` stores verbatim as that participant's ECDH public key. No proof of possession or non-identity check is performed on `enc_key`. A participant that registers the group identity (or another point whose discrete log relationship is degenerate) causes every honest sender's `encrypt` to derive the ChaCha20 key from `ecdh(k, identity) = identity` — a publicly known point — so the ChaCha20 keystream is computable by anyone who observes the ciphertext.

### Finding Description
The bug class of the report (attacker-supplied input reaching a sensitive operation unsanitized) maps onto the ECDH key registration path:

- `EncryptionKeyMessage::read` accepts arbitrary `enc_key` bytes via `C::read_G` with no semantic validation (crypto/dkg/pedpop/src/encryption.rs:57-58).
- `Decryption::register` stores it with no PoP/PoK and no identity/torsion check (encryption.rs:351-362). Contrast with `EncryptedMessage`, which carries an explicit `pop` Schnorr signature precisely because the code acknowledges unauthenticated key claims are dangerous (encryption.rs:83-93) — the same protection was never applied to the long-term `enc_key`.
- In `verify_r1`/`KeyMachine`, each sender calls `self.encryption.encrypt(rng, l, share_bytes)` → `encrypt(...)` → `cipher(context, &ecdh(&key, to))` where `to = enc_keys[&l]` (encryption.rs:154, 466). `ecdh` is a plain scalar multiplication (encryption.rs:95-97). If `to` is the identity, the shared secret is the identity point, independent of the ephemeral key.
- `cipher` then derives the ChaCha20 key solely from `context` and the encoding of that shared point (encryption.rs:101-133). `context` is public and the identity encoding is public, so the keystream is publicly derivable, and the IV is static (`b"DKG IV v0.2\0"`).

The `Commitments` PoK (`challenge` over context/participant/nonce/commitments at pedpop/src/lib.rs:86-94) only proves knowledge of `coefficients[0]`; it never binds `enc_key`.

### Impact Explanation
Every `EncryptedMessage` addressed to the malicious participant is encrypted under a publicly known keystream. Any passive observer of the authenticated channel recovers every sender's polynomial share `f_j(victim_index)` — i.e., key-share recovery of all shares destined for that index, which sums to that participant's full secret share. This breaks the DKG's core confidentiality guarantee and, combined with other compromised shares (or reducing the effective threshold an attacker must defeat), directly enables group secret reconstruction. It matches the "key share recovery" acceptance criterion and is reachable purely via a crafted `EncryptionKeyMessage` fed to `EncryptionKeyMessage::read`.

### Likelihood Explanation
The malicious participant only needs to be a member of the DKG and submit `enc_key = identity` (valid canonical encoding on the in-scope groups) inside their otherwise-valid `EncryptionKeyMessage`. Nothing in `verify_r1`, `register`, or the batch verification rejects it; the protocol completes normally while the victim's shares are plaintext-equivalent. One residual uncertainty: whether a given ciphersuite's `read_G` rejects the identity encoding — for the dalek-ff-group and kp256 backends canonical identity encodings are accepted, but I could not fully re-verify each backend's check in this pass.

### Recommendation
Require a proof of possession/knowledge for `enc_key` in `EncryptionKeyMessage` (e.g., a Schnorr signature over `context || participant` keyed to `enc_key`), and explicitly reject the identity and non-prime-order/torsioned points in `Decryption::register` before storing the key.

### Proof of Concept
1. Malicious participant `m` builds `EncryptionKeyMessage` with a valid `Commitments` PoK but `enc_key = C::G::identity()` and broadcasts it.
2. Every honest sender `j` computes `shared = ecdh(random_k_j, identity) = identity` and encrypts `f_j(m)` with `cipher(context, identity)`.
3. An observer recomputes the same ChaCha20 key from the public `context` and the public identity encoding, XORs the ciphertext, and recovers every `f_j(m)` in cleartext, yielding participant `m`'s secret share `Σ f_j(m)`.