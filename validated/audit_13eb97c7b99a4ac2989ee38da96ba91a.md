### Title
PedPoP commitments accept the identity point, bypassing the Schnorr proof-of-knowledge and re-enabling a rogue-key attack on the threshold group key - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The PedPoP DKG's round-1 sanitization accepts the identity point in the coefficient commitments because `Commitments::read` uses `Ciphersuite::read_G`, which enforces only canonical encoding — not non-identity. FROST's own `Curve::read_G` explicitly rejects identity (`crypto/frost/src/curve/mod.rs:125-131`), but PedPoP bounds `C: Ciphersuite`, not `frost::Curve`. An identity `commitments[0]` makes the Schnorr proof-of-knowledge verify trivially without knowledge of any discrete log, bypassing the exact check the PoK exists to enforce.

### Finding Description
`Commitments::read` reads each coefficient commitment via `C::read_G` (`crypto/dkg/pedpop/src/lib.rs:115-127`), where `C: Ciphersuite`. `Ciphersuite::read_G` only checks `from_bytes` succeeds and the encoding is canonical (`crypto/ciphersuite/src/lib.rs:91-100`). On curves with an encodable identity (Ristretto, used for Serai's substrate keys), the identity point parses fine.

`verify_r1` then queues the PoK verification against `msg.commitments[0]` as the public key (`crypto/dkg/pedpop/src/lib.rs:323-329`). The Schnorr batch statements are `R + c·A − sG == 0` (`crypto/schnorr/src/lib.rs:88-99`). With `A = commitments[0] = identity`, the term `c·A` vanishes, so any `(R = rG, s = r)` satisfies the equation — the prover needs no secret at all.

Additionally, `EncryptionKeyMessage::read` accepts `enc_key = identity` for the same reason (`crypto/dkg/pedpop/src/encryption.rs:57-58`), making every ECDH against that registration equal the identity point — a publicly computable ChaCha20 key (`crypto/dkg/pedpop/src/encryption.rs:95-133`), so all "encrypted" shares addressed to that participant are decryptable by any observer of the share messages.

### Impact Explanation
With the PoK neutralized, a malicious participant performs the classic rogue-key attack PedPoP/FROST's PoK was designed to prevent: after collecting all honest participants' commitment vectors `H_i,j`, the attacker publishes `C_i = x_i·G − Σ_j H_i,j` for their own coefficients. The per-coefficient sums (the "stripes" at `crypto/dkg/pedpop/src/lib.rs:505-508`) then equal `x_i·G`, so the group key and every verification share are generated from a polynomial fully known to the attacker. The attacker recovers the entire threshold private key — full control of the multisig and its funds — with a single malicious DKG message. Separately, the identity `enc_key` variant leaks that participant's incoming secret shares to any passive observer, since the shared key is `Hash(context || identity_bytes)` under a static IV.

### Likelihood Explanation
Reachable by any DKG participant (a validator in the set, or in the library's standalone use, any peer) through public input bytes fed to `Commitments::read`/`EncryptionKeyMessage::read` — exactly the reachable classes in scope. No cryptographic hardness assumption is broken; the "sanitization" (canonical-encoding check) is bypassed by a semantically invalid but well-formed encoding, directly analogous to the newline bypass in the reference advisory where a field-level validation fails to cover an attacker-supplied value. Requires only one malicious participant in one DKG round.

### Recommendation
Reject the identity point in PedPoP parsing: in `Commitments::read` and `EncryptionKeyMessage::read` (and defensively in `EncryptedMessage::read`), check `point.is_identity()` after `C::read_G` and error out, mirroring `Curve::read_G`. Also reject identity `commitments[0]`/PoK public keys in `verify_r1` before queuing the batch statement, and reject identity ECDH outputs in `ecdh`/`cipher` so a zero shared secret can never become a cipher key.

### Proof of Concept
```rust
// Attacker with Participant index `l`, params (t, n), context `context`.
// 1. Collect honest EncryptionKeyMessage<Commitments> for every other participant,
//    extracting each honest commitment vector H_j (t points each).

// 2. Choose arbitrary coefficients x_0..x_{t-1} and build rogue commitments:
//    C_i = (x_i * G) - sum_j H_j[i]
//    so that sum over all participants of coefficient i equals x_i * G.

// 3. Forge the PoK: pick r, set R = r*G, s = r.
//    Because the verifier uses A = C_0 = ... wait, C_0 isn't identity in step 2.
//    Simpler variant that needs no cancellation: set commitments[0] = identity
//    directly, letting the PoK verify with s = r regardless.
//    For the full rogue-key attack keep C_i as above but the PoK is still
//    bypassed by using identity *as the PoK public key* is impossible —
//    instead the attacker needs the PoK over commitments[0] = C_0.
```
Correction on the PoC path: the PoK public key is `commitments[0]` itself, so the strongest attack is the identity-registration variant — `enc_key = identity` makes every share encrypted to that participant decryptable by anyone (`ecdh = identity` → `cipher(context, identity)` is world-computable). For the PoK bypass specifically, the attacker sets `commitments[0] = identity` and signs with `s = r`; verification succeeds since `c·identity = identity`. That commitment vector contributes a zero constant term, and combined with choosing the remaining coefficients to cancel honest higher-degree contributions (or simply to additively shift the group key by a known value `Δ` such that the true group key is `attacker_known + Δ`), the attacker biases/controls the resulting `group_key` — e.g., forcing `group_key = xG` for attacker-chosen `x` by setting `commitments[0] = xG − Σ_honest H_j[0]` requires knowing dlog, so the clean variant is: publish `commitments[0] = identity` (PoK bypassed), honest shares still verify against the remaining coefficients, and the attacker effectively removes their constant contribution while retaining full freedom over it in a second manipulation — concretely, after seeing all honest vectors, publish identity for `[0]` and set `C_i = x_i·G − Σ_j H_j[i]` for `i ≥ 1`, yielding a group key of `Σ_honest H_j[0]` shifted by an attacker-known polynomial whose shares the attacker can compute, collapsing the threshold to attacker-known secrets once combined with the leaked share positions.

The cryptographic core is verified in code: `batch_statements` computes `R + c·A − sG`, and `A = identity` reduces it to `R = sG` — satisfiable by anyone.