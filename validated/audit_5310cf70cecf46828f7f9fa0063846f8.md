### Title
`ThresholdKeys::read` accepts identity verification shares, yielding an identity group key that validates forged signatures - (File: crypto/dkg/src/lib.rs)

### Summary
The reported bug class is an API that silently returns/accepts a degenerate "zero" value which downstream logic consumes without validation, corrupting all dependent computation. The Serai analog lives in `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`: serialized verification shares are deserialized with `Ciphersuite::read_G`, which — unlike the FROST-specific `Curve::read_G` — does **not** reject the identity (zero) point. An untrusted `ThresholdKeys` blob can therefore carry identity verification shares, which causes the group key to be computed as the identity point. Any Schnorr/FROST signature `R = s·G` then verifies against this "group key," enabling universal forgery.

### Finding Description
`ThresholdKeys::read` reads `n` verification shares with `<C as Ciphersuite>::read_G(reader)`:

- `crypto/dkg/src/lib.rs:620-623` — `verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);`

`Ciphersuite::read_G` only checks canonical encoding, not identity:

- `crypto/ciphersuite/src/lib.rs:91-100` — rejects non-canonical encodings but accepts the identity point.

By contrast, FROST's `Curve::read_G` explicitly rejects identity:

- `crypto/frost/src/curve/mod.rs:123-131` — `if res.is_identity().into() { Err(...) }`.

`ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) performs structural checks (count, `i <= n`, interpolation applicability) but never checks that any verification share is non-identity. It computes the group key directly from the shares:

- `crypto/dkg/src/lib.rs:376-378` — `group_key = Σ verification_shares[i] * interpolation_factor(i, 1..=t)`.

If all `t` shares used are identity, `group_key == identity`. `ThresholdView::group_key` then returns `(identity * 1) + G*0 = identity` (`crypto/dkg/src/lib.rs:445-447`).

The resulting `ThresholdKeys` flows into `AlgorithmMachine`/`AlgorithmSignMachine`, where `verify` calls `SchnorrSignature::verify(group_key, c)`, i.e. `R + c·A − s·G == 0` (`crypto/schnorr/src/lib.rs:88-110`). With `A = identity`, any `(R = s·G, s)` satisfies the equation for any challenge — a forged signature that passes `verify` (`crypto/frost/src/algorithm.rs:213-217`), and share-level `batch_statements` checks also degenerate (`(challenge, identity)` contributes nothing).

This mirrors the oracle bug exactly: `latestAnswer` returns `0` instead of reverting → free transactions; `read_G`/`ThresholdKeys::new` accept the `0` (identity) point instead of rejecting → a "group key" under which signatures are trivially forgeable.

### Impact Explanation
An attacker who can feed crafted bytes to `ThresholdKeys::read` produces a fully "valid" `ThresholdKeys` whose group key is the identity point. Every downstream signing path (FROST `AlgorithmSignMachine::sign`, `AlgorithmSignatureMachine::complete`, `Schnorr::verify`) treats signatures for this key as verifiable, and anyone can forge a signature for the identity key with arbitrary `s`. Where these keys gate funds (e.g., Bitcoin `SignableTransaction`/scanner outputs, Ethereum router commands), this translates to signatures authorizing spends/messages under a key the attacker fully controls — analogous to the report's "free transactions."

### Likelihood Explanation
Exploitation requires that an untrusted party's serialized `ThresholdKeys` blob reaches `ThresholdKeys::read` — the prompt's own reachability list includes untrusted bytes fed to `ThresholdKeys::read`. Key material is normally locally persisted, so practical exposure depends on deployments that import keys/shares from external sources; the defect itself (missing identity rejection in deserialization) is unconditional.

### Recommendation
Reject identity verification shares in `ThresholdKeys::new` (covering `ThresholdKeys::read` and all constructors), or use `Curve::read_G`-equivalent identity rejection when deserializing verification shares in `ThresholdKeys::read`. Additionally, assert `group_key.is_identity() == false` after interpolation in `ThresholdKeys::new` as a defense-in-depth sanity check — the same "check the returned value isn't the degenerate zero" fix as recommending `latestRoundData` + `updatedAt` checks.

### Proof of Concept
Conceptually: serialize a `ThresholdKeys<Secp256k1>` (or `Ristretto`) blob where every verification share entry is the canonical identity encoding. `ThresholdKeys::read` succeeds (all points canonical), `ThresholdKeys::new` computes `group_key = identity`. Then `SchnorrSignature { R: G * 7, s: 7 }.verify(identity, any_challenge)` returns `true`, since `R + c·I − s·G = 0` for all `c`. A FROST `Schnorr` algorithm machine built on these keys will emit/accept signatures for the identity group key.

Key code paths:
- `crypto/dkg/src/lib.rs:618-631` — `ThresholdKeys::read` reads `secret_share` and `verification_shares` with `C::read_F`/`C::read_G`, then calls `ThresholdKeys::new`.
- `crypto/dkg/src/lib.rs:376-378` — group key derived from unchecked shares.
- `crypto/schnorr/src/lib.rs:88-110` — verification formula degenerates when `public_key` is identity.