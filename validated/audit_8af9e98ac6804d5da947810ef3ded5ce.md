### Title
`SchnorrAggregate::verify` derives aggregation weights from challenges only, allowing trivial forgery of aggregate signatures for arbitrary keys - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
The bug class in the external report is a sentinel/default write resetting a "consumed" marker so a check passes again — a binding/check computed over data that omits the value it is supposed to constrain. The Serai analog lives in the half-aggregation verifier: `SchnorrAggregate::verify` computes the per-signature weights `z_i` from a transcript that absorbs only the caller-supplied `challenges`. The nonce commitments `Rs` and the public keys being verified are never bound into the weight derivation. An unprivileged party can therefore craft `SchnorrAggregate` bytes (via `SchnorrAggregate::read`) that satisfy the verification equation for *any* set of public keys and challenges, producing a forged aggregate signature.

### Finding Description
`weight()` draws each `z_i` by calling `digest.challenge(b"aggregation_weight")` on a `DigestTranscript` initialized only with the caller's `dst`, a `"signatures"` domain separator, and the list of `challenges` (crypto/schnorr/src/aggregate.rs:132-143). The verification equation is `Σ z_i·R_i + Σ z_i·c_i·K_i − s·G = 0`. Because every `z_i` is fully determined before the verifier ever looks at `Rs` or `s`, and none of the `z_i` depend on `R_i` or `K_i`, the equation has a single linear unknown the prover controls: pick any `s` and any `R_i` for `i ≠ j`, then solve for

`R_j = z_j^{-1}·(s·G − Σ_{i≠j} z_i·R_i − Σ_i z_i·c_i·K_i)`

All quantities on the right are public and computable by the attacker (`z_j` is invertible; a `z_j = 0` outcome has negligible probability and can be sidestepped by perturbing `s`'s position choice or re-solving a different index). `SchnorrAggregate::read` places no constraint on `Rs` beyond decodability, so the forged `R_j` is fed to `verify` directly. This reproduces the report's pattern: a check intended to be unique-per-item (each weight binding one signature) is computed from state that doesn't include the item, so arbitrary "items" pass.

### Impact Explanation
`verify` returns `true` for a `SchnorrAggregate` constructed by someone who knows none of the private keys and never observed honest signatures. Any caller relying on `SchnorrAggregate::verify` as proof that all `(key, challenge)` pairs signed accepts a forgery — a complete break of the signature scheme's unforgeability. Severity: High (forged proof/signature reachable entirely from attacker-supplied bytes to `read`/`verify`).

### Likelihood Explanation
Any party that can submit a serialized `SchnorrAggregate` to a verifier can perform the forgery; it requires only field/point arithmetic on public values, no secret knowledge, no collusion, and no special timing. Exploitation is deterministic and unconditional once the verifier path is reached.

### Recommendation
Bind every signature component into the weight transcript before drawing `z_i`: inside the loop (or once, before the loop), `append_message` each `R_i`'s `to_bytes()` and each key's `to_bytes()` in addition to the challenge — e.g., transcript `b"key"`, `b"R"`, `b"challenge"` per index — so that `z_i = H(dst, all keys, all Rs, all challenges, i)` as in the half-aggregation construction of eprint 2021/350. Optionally also reject identity `R_i` on read to eliminate degenerate terms.

### Proof of Concept
For keys `K_1..K_n` with challenges `c_1..c_n` supplied by the verifier's caller:

1. Locally instantiate `DigestTranscript::<C::H>::new(dst)`, `domain_separate(b"signatures")`, `append_message(b"challenge", c_i.to_repr())` for each `i` — replicating crypto/schnorr/src/aggregate.rs:132-136.
2. Draw `z_1..z_n` via the same `weight(&mut digest)` sequence — these are computable by the attacker since the transcript state depends on nothing secret.
3. Choose `s = C::F::ZERO` and `R_i = C::G::identity()` (or random points) for `i ≥ 2`.
4. Compute `R_1 = z_1^{-1}·(s·G − Σ_{i≥2} z_i·R_i − Σ_i z_i·c_i·K_i)`.
5. Serialize `SchnorrAggregate { Rs: [R_1..R_n], s }`; `SchnorrAggregate::read` then `verify(dst, keys_and_challenges)` returns `true` — an aggregate signature "valid" for keys whose owners never signed anything.