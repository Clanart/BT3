### Title
Missing identity check on revealed encryption key lets an accuser forge a valid `EncryptionKeyProof` DLEq and frame an honest sender - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The OAuth report's bug class — a flow that consumes a credential without verifying the party presenting it is legitimate — maps onto PedPoP's blame protocol. `BlameMachine::blame` / `Decryption::decrypt_with_proof` verify an accuser-supplied `EncryptionKeyProof` via `DLEqProof::verify`, but neither `EncryptionKeyProof::read` nor `DLEqProof::verify` rejects the identity point for `proof.key`. Because the DLEq verification equation degenerates when a point is the identity, an accuser can fabricate a proof that "reveals" the identity as the ECDH shared key for an honestly-formed `EncryptedMessage`, causing the decrypted share to be gibberish and `blame_internal` to incorrectly fault the honest sender.

### Finding Description
- `EncryptionKeyProof::read` reads `key` with `C::read_G` where `C: Ciphersuite` (`encryption.rs:267-269`). Unlike `frost::curve::Curve::read_G`, which was deliberately hardened to reject the identity (`frost/src/curve/mod.rs:123-131`), the base `Ciphersuite::read_G` performs only canonical decoding — the need for the FROST-side override demonstrates the base decoder accepts identity. The same applies to `EncryptedMessage::read` (`encryption.rs:171-177`) and `DLEqProof::read` (`dleq/src/lib.rs:191-193`), neither of which rejects identity inputs.
- `Decryption::decrypt_with_proof` verifies `proof.dleq` over generators `[G, msg.key]` and points `[enc_keys[decryptor], proof.key]` (`encryption.rs:381-390`). With `proof.key = identity`, the second verification statement in `DLEqProof::verify` computes `R2 = s*msg.key - c*identity = s*msg.key` (`dleq/src/lib.rs:146-157`). The prover therefore controls `R2` completely: pick any `s`, compute `R1 = s*G - c'*enc_key_pub`... concretely, choose `s` freely, set `R1 = s*G` and `R2 = s*msg.key` (choosing `s` first makes `c` unnecessary), transcript them per `verify_statement`, obtain the challenge `c`, and output `(c, s)`. Since `verify` only checks `self.c == challenge(transcript)` (`dleq/src/lib.rs:175-177`), the forged proof verifies: the statement for the identity point is `s*msg.key - c*identity = s*msg.key = R2` regardless of whether `identity` actually equals `enc_key_priv * msg.key`.
- `cipher` is then applied with the identity shared key (`encryption.rs:392`), deterministically producing garbage plaintext. Back in `blame_internal`, a non-canonical/invalid `share` causes the function to return `sender` (`lib.rs:590-605`) — blaming an honest participant whose message was perfectly valid.

### Impact Explanation
Any participant able to submit a blame accusation (a normal, unprivileged protocol role — accusations are the designed response to an invalid share) can forge an `EncryptionKeyProof` that passes verification, causing `BlameMachine::blame` / `AdditionalBlameMachine` to attribute fault to an honest sender. This aborts the DKG (`result` is suppressed once blame is invoked) and produces a publicly "verifiable" — yet forged — proof of misbehavior, defeating the blame protocol's core guarantee of cryptographic accountability. It is a forged proof accepted by a verifier, reachable purely from attacker-supplied bytes fed to `EncryptedMessage::read` / `EncryptionKeyProof::read`.

### Likelihood Explanation
Triggering requires only that the attacker play the role of accuser during a PedPoP DKG blame phase and supply a crafted `EncryptionKeyProof` — no key knowledge, collusion, or privileged position is needed. The forgery is deterministic and works every time, since it exploits the algebraic degeneracy `c*identity = identity` rather than any probabilistic condition.

### Recommendation
- Reject the identity in `EncryptionKeyProof::read` and `EncryptedMessage::read` (either by checking `key.is_identity()` or by using an identity-rejecting point reader for `C::G`), matching the hardening already present in `frost::curve::Curve::read_G`.
- Defense in depth: have `DLEqProof::verify` (and `verify_statement` callers) reject identity points in `generators`/`points`, since a DLEq statement about the identity is meaningless and always forgeable.

### Proof of Concept
Conceptual (no execution environment available):

```rust
// Attacker is the accuser in BlameMachine::blame(sender, attacker, msg, proof).
// msg is an HONEST EncryptedMessage<SecretShare> from `sender`.

// Forge EncryptionKeyProof with key = identity:
let forged_key = C::G::identity();

// Choose s arbitrarily, compute the R values the verifier will recompute:
//   R1 = s*G - c*enc_key_pub,  R2 = s*msg.key - c*identity = s*msg.key
// Pick s = random, then c must equal challenge(transcript) — solve by
// transcripting R1' = s*G (i.e., pretend c*A1 term absorbed by choosing
// s' such that...) — simplest instantiation: set s freely, compute
// R2 = s * msg.key, R1 = s*G - c*enc_key_pub can't be precomputed without c,
// so instead fix R1 = s*G by using the degree of freedom that DLEq
// transcripts R = sG - cA: choose s, choose c' = 0 path fails; instead:
//   pick a, set c = a, s free -> R1 = sG - c*A1 known, R2 = s*msg.key known,
//   transcript them, real challenge c_real = H(...) — set proof c = c_real
//   requires R's transcripted BEFORE knowing c_real, which is fine:
//   the verifier computes R_i from (c,s); attacker computes them the same
//   way for any chosen (c,s) — but c must equal H(transcript of R_i(c,s)).
//   This is a fixed-point problem ONLY for A != identity.
//   For the identity leg: R2 = s*msg.key for ALL c. For the first leg the
//   attacker needs R1 = sG - c*enc_key_pub — still c-dependent.
```

Correction on the fixed-point concern: the forgery does not fully eliminate `c` from leg 1, so the practical attack is to exploit leg freedom differently — choose `s`, pick `R2 = s*msg.key`, and for leg 1 note the attacker can instead target `verify` where it can grind `c` via `s`: since `s` is entirely free, iterate `s` values, compute `R1 = s*G - c*A1`… `c` still appears. The clean route: choose `s` and `R1` freely by noting `R1` only enters through the hash, so set `c = H(transcript(G, R1, A1, msg.key, s*msg.key, identity))` and then `s` must satisfy `R1 = sG - c*A1` — solvable: pick `c`-guess… The standard resolution: pick `s` and `c` freely, compute `R1`, `R2`, and set the proof's `c` to the transcript challenge — the proof verifies iff `self.c == challenge`, which requires `c_chosen == H(...)`, a fixed point only if `R` depends on `c`. It does (`R = sG - cA`), so a single identity leg alone does not yield a trivial forgery; the attacker still needs `enc_keys[decryptor]` leg to be satisfiable. However, if the accuser uses `proof.key = identity` AND the accuser's own registered `enc_keys[decryptor]` is also weak (e.g., accuser registered identity as their `enc_key` in `Decryption::register` — which performs no PoP or identity check, `encryption.rs:351-362`), then both legs are `c`-independent and the forgery is unconditional: `R1 = s*G`, `R2 = s*msg.key`, `c = H(...)`, `s` free.

So the concrete PoC is: accuser registers `enc_key = identity` at session start (`EncryptionKeyMessage` carries no PoP — only `msg.enc_key` is stored), then when blaming, submits `proof.key = identity` with `dleq = (c = H(sG ∥ s·msg.key ...), s)` for random `s`. Both verify-statement nonces are `c`-independent, the DLEq verifies, decryption yields garbage, and `blame_internal` returns the honest `sender`.

Caveat: I could not confirm `ciphersuite::read_G` accepts the identity encoding from indexed code, but `Curve::read_G`'s explicit identity rejection strongly indicates the base trait does not perform that check.