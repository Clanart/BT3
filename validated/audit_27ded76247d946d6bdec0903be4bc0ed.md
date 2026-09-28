### Title
Attacker can swap the unsigned `enc_key` in a PedPoP `EncryptionKeyMessage<Commitments>`, causing a victim's DKG shares to be encrypted to the attacker — key share theft and false blame - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP DKG authenticates a participant's commitment polynomial via a Schnorr proof of knowledge whose challenge covers `context`, `participant`, the nonce `R`, and `cached_msg` — the raw bytes of the commitment points only. The `enc_key` field of `EncryptionKeyMessage`, which every peer will later ECDH against to encrypt that participant's secret shares, is appended to the message *outside* the signed/committed region. Anyone able to deliver a modified copy of a participant's broadcast message (the same "Eve injects a message" threat the code explicitly defends against in `EncryptedMessage`'s PoP commentary) can substitute `enc_key` with their own encryption key. All shares destined for the victim are then encrypted to the attacker, who recovers the victim's threshold secret share — while the blame machinery misidentifies the *victim* as the faulty party.

### Finding Description
`Commitments` consists of `commitments`, `cached_msg`, and a Schnorr `sig` (`crypto/dkg/pedpop/src/lib.rs:103-107`). The PoK challenge is `challenge::<C>(context, l, R, &msg.cached_msg)` — i.e., it binds only the serialized commitment points (`crypto/dkg/pedpop/src/lib.rs:86-94, 323-329`). The surrounding `EncryptionKeyMessage` carries `enc_key` as a separate, unauthenticated field: `read` parses `msg` and `enc_key` independently (`crypto/dkg/pedpop/src/encryption.rs:57-59`), and `register` stores whatever `enc_key` arrived under the claimed participant index (`crypto/dkg/pedpop/src/encryption.rs:351-362`).

Concretely:

1. Alice broadcasts `EncryptionKeyMessage { msg: Commitments{...Alice's PoK...}, enc_key: A_enc_pub }`.
2. An attacker Eve copies the message verbatim, replaces `enc_key` with `E_enc_pub` (for which Eve knows `e`), and delivers it to Bob keyed under `Participant = Alice`. The PoK still verifies — `verify_r1` recomputes the challenge over `l`, `R`, and `cached_msg`, none of which include `enc_key` (`crypto/dkg/pedpop/src/lib.rs:313-334`).
3. Bob stores `enc_keys[Alice] = E_enc_pub` via `Decryption::register`, then encrypts Alice's secret share with `ecdh(key, E_enc_pub)` (`crypto/dkg/pedpop/src/encryption.rs:154, 460-467`). Every other honest participant does the same.
4. Eve decrypts every `EncryptedMessage` addressed to Alice: the cipher key is derived from `k * E_enc_pub` and Eve knows `e` such that `E_enc_pub = e·G`... equivalently Eve computes `e * msg.key` since `msg.key = k·G` (`crypto/dkg/pedpop/src/encryption.rs:95-97, 487-488`). Summing the decrypted shares yields Alice's final secret share `Σ f_j(Alice)` (`crypto/dkg/pedpop/src/lib.rs:476-491`).
5. When Alice fails to decrypt, blame resolution makes it worse: `decrypt_with_proof` requires a DLEq proving `proof.key = enc_key_priv * msg.key` against `enc_keys[decryptor]` (`crypto/dkg/pedpop/src/encryption.rs:381-395`). But `enc_keys[Alice]` now holds Eve's key, not `a_enc·G`, so Alice's honest `EncryptionKeyProof` fails verification and `blame_internal` returns `recipient` — Alice is wrongly blamed (`crypto/dkg/pedpop/src/lib.rs:582-608`).

The code itself establishes the relevant threat model: the PoP on `EncryptedMessage` exists precisely because "Eve could observe Alice encrypt to Bob with key X, then send Bob a message also claiming to use X" (`crypto/dkg/pedpop/src/encryption.rs:83-90`) — i.e., the library intends to resist injected/relayed messages, yet the same protection was not extended to the `enc_key` registration field, which has no proof-of-possession and no binding to the signed commitments.

### Impact Explanation
Recovery of a participant's threshold secret share is listed as an in-scope impact. With Alice's secret share, Eve can produce valid FROST signature shares under Alice's `Participant` index (share verification checks `s·G == λ_i·verification_share_i + bound-nonce terms` — all satisfiable by the true share), effectively signing "as a different user," and Eve learns enough to combine with other compromise paths toward full group-key recovery. Additionally, the blame protocol provably punishes the honest victim, giving Eve both secret theft and a framing primitive. This mirrors CVE-2018-1331's shape: an authenticated-context action is executed under a different security principal because identity-relevant material (`enc_key`) is not bound to the identity proof.

### Likelihood Explanation
Requires the attacker to deliver a modified copy of a victim's round-1 `EncryptionKeyMessage` to at least one honest participant — e.g., a network MitM, a malicious relay/coordinator, or any deployment where the "authenticated channel" assumption is weaker for forwarding than for origin (the exact gap the PoP commentary acknowledges for the later round). No knowledge of any secret is needed, and PoK verification does not detect the substitution. It is partially mitigated if integrators run the DKG over fully authenticated, tamper-proof channels — but the library's own design shows that channel is not trusted for key provenance ("the per-message keys have no root of trust other than ... the assumed-to-exist external authenticated channel," `crypto/dkg/pedpop/src/encryption.rs:300-301`), and one compromised forwarding path suffices.

### Recommendation
Bind `enc_key` to the participant's identity and the PoK: include the serialized `enc_key` inside `cached_msg` (or append it to the `challenge` transcript in `crypto/dkg/pedpop/src/lib.rs:86-94`), and/or require a Schnorr proof of possession for `enc_key` in `EncryptionKeyMessage`, verified in `Decryption::register` / `verify_r1` before the key is stored. `register` should also reject `enc_key` values that are identity or duplicates of another participant's key.

### Proof of Concept
```rust
// EVE, upon seeing Alice's broadcast EncryptionKeyMessage<Commitments>:
let mut raw = alice_registration.serialize();
// Layout: [cached commitments bytes (t * G_len)] || schnorr sig || enc_key (G_len)
// Overwrite only the trailing enc_key with E_enc_pub = e * G:
let g_len = <C::G as GroupEncoding>::Repr::default().as_ref().len();
let off = raw.len() - g_len;
raw[off..].copy_from_slice(eve_enc_pub.to_bytes().as_ref());
// Deliver to Bob under Participant = Alice:
let msg = EncryptionKeyMessage::<C, Commitments<C>>::read(
    &mut raw.as_slice(), bob_params).unwrap();
// Bob's verify_r1 passes: challenge(context, Alice, R, cached_msg) is unchanged,
// since cached_msg excludes enc_key.
bob_machine.register(Alice, msg); // enc_keys[Alice] = E_enc_pub
// Bob's share for Alice is ChaCha20-encrypted under cipher(context, k*E_enc_pub).
// Eve computes e * msg.key (msg.key = k*G), derives the same cipher, decrypts the
// SecretShare. Repeating for all senders yields sum_j f_j(Alice) = Alice's share.
// When Alice complains, blame_internal returns `recipient` (InvalidProof), blaming Alice.
```
Root cause lines: unsigned `enc_key` read at `crypto/dkg/pedpop/src/encryption.rs:57-59`, stored at `crypto/dkg/pedpop/src/encryption.rs:356-361`; PoK challenge omits it at `crypto/dkg/pedpop/src/lib.rs:86-94` and `323-329`; share encryption consumes the attacker key at `crypto/dkg/pedpop/src/encryption.rs:466`; victim mis-blamed at `crypto/dkg/pedpop/src/lib.rs:586-587`.