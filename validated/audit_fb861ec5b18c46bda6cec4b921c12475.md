### Title
`ThresholdKeys` accepts identity verification shares and zero secret shares without validation, allowing an attacker-supplied key set whose group key is the identity point ("zero address") — (File: crypto/dkg/src/lib.rs)

### Summary
The external report's bug class is *a security-critical parameter is accepted as zero (the zero address) without a check*. In Serai, the analog lives in `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`. Serialized key material read via `ThresholdKeys::read` parses each verification share with `<C as Ciphersuite>::read_G`, which enforces only canonical encoding and explicitly accepts the identity point — unlike `Curve::read_G` in `crypto/frost/src/curve/mod.rs:123-131`, which rejects identity. `ThresholdKeys::new` then derives `group_key` from participants `1..=t` with no check that the resulting group key is non-identity, that any verification share is non-identity, or that `secret_share` is consistent with `verification_shares[params.i]`. The result: untrusted bytes fed to `ThresholdKeys::read` can instantiate a threshold key whose reported `group_key` is the curve identity — the exact group-theoretic analog of a zero address.

### Finding Description
`Ciphersuite::read_G` in `crypto/ciphersuite/src/lib.rs:91-101` decodes a point and rejects non-canonical encodings, but never rejects `G::identity()`. `ThresholdKeys::read` in `crypto/dkg/src/lib.rs:620-623` uses it to populate `verification_shares`, and `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) validates only the *count* and *index range* of the shares (`len() == n`, each `participant <= n`), then computes the group key as

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

(`crypto/dkg/src/lib.rs:376-378`). There is no check that `group_key` is non-identity, that any `verification_shares[i]` is non-identity, or that `C::generator() * secret_share == verification_shares[params.i]` — the secret share is read at `crypto/dkg/src/lib.rs:618` via `C::read_F`, which also accepts zero.

Compare with the rest of the stack, which *does* treat identity as a poison value precisely because it breaks signatures: `Curve::read_G` rejects identity (`crypto/frost/src/curve/mod.rs:123-131`), `Curve::random_nonce` rejection-samples zero nonces because zero commitments "leak the secret share" (`crypto/frost/src/curve/mod.rs:103-117`), `Signed::read` rejects an identity `R` (`coordinator/tributary/src/transaction.rs:62-69`), `BatchVerifier::queue` re-samples zero weights since "a zero scalar would cause this item to pass no matter what" (`crypto/multiexp/src/batch.rs:82-84`), and `ThresholdParams::new`/`Participant::new` reject zero parameters (`crypto/dkg/src/lib.rs:29-35`). The identity acceptance in the trusted-key deserialization path is inconsistent with this posture.

The danger is amplified by a second unchecked property: `group_key` is derived exclusively from the shares of participants `1..=t`, but `ThresholdView`s for signing sets that exclude any of `1..=t` interpolate a *different* key if the supplied verification shares do not all lie on one polynomial — nothing in `ThresholdKeys::new` verifies cross-share consistency. `view()` (`crypto/dkg/src/lib.rs:463-533`) happily returns a view whose effective group key differs from `self.group_key()` returned to callers.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (explicitly in-scope per the untrusted-bytes rule) produces a `ThresholdKeys` with:

- **Identity group key** — all verification shares set to identity yields `group_key == identity`. The group key is the deposit/verification address downstream consumers report and protect. Funds sent to an identity key are spendable by *anyone* (the discrete log of identity is 0; `SchnorrSignature { R: s·G, s }` trivially verifies under `A = identity` since `R + c·A − s·G = 0`), so assets "received" at this key are unilaterally spendable by the attacker or valueless — the exact zero-address impact from the source report, transposed onto curve points.
- **Attacker-controlled group key** — the attacker chooses a polynomial with known coefficients, writes the corresponding verification shares and a victim `secret_share`, and the victim's `group_key()` resolves to a key the attacker alone controls while appearing to be a genuine t-of-n key. `view()`/`sign`/`complete` then produce signature shares consistent with the attacker's key, and deposits are stealable without threshold participation.
- **Inconsistent signing sets** — verification shares on different polynomials make `group_key()` (from `1..=t`) disagree with the key interpolated by other signing sets, so the reported deposit address may be unspendable by the declared threshold even though `view()` succeeds and produces signatures that verify under a different key — funds reported received that are not spendable.

### Likelihood Explanation
Reachability is by construction: `ThresholdKeys::read` is a listed untrusted-bytes entry point, and every required condition (identity shares, zero secret share, inconsistent polynomials) is expressible in canonical encodings that pass `read_G`/`read_F`. No malformed encoding, collusion, or privileged position is needed — just the ability to supply serialized key material. The checks that would catch this (identity rejection in `read_G`, share/share-secret consistency, cross-polynomial consistency) are simply absent, so triggering is deterministic rather than probabilistic. Confidence is medium: whether a given Serai consumer actually deserializes attacker-controlled `ThresholdKeys` (rather than only locally generated ones) determines practical exploitability, and PedPoP/dealer-generated keys are self-consistent because PedPoP batch-verifies shares (`crypto/dkg/pedpop/src/lib.rs:487-499`) — the bug bites exactly when the read path is exposed to untrusted input, which the scope rules treat as reachable.

### Recommendation
- In `ThresholdKeys::read` (or a dedicated strict reader for key material), reject identity verification shares — either use an identity-rejecting read like `Curve::read_G` or check `is_identity()` per share, mirroring `Signed::read`'s defense.
- In `ThresholdKeys::new`, reject `secret_share == 0`, verify `C::generator() * secret_share == verification_shares[&params.i()]`, and reject a resulting `group_key` that `is_identity()`.
- Verify all `verification_shares` lie on a single degree-`t-1` polynomial (e.g., check that every `t`-subset interpolates to the same `group_key`, or that the Lagrange reconstruction over a second disjoint `t`-subset equals the first) so `group_key()` is invariant across signing sets, eliminating the `1..=t`-only derivation hazard.

### Proof of Concept
Conceptual byte-level construction against `ThresholdKeys::<C>::read`:

```text
buf  = u32_le(C::ID.len()) || C::ID
    || u16_le(t=2) || u16_le(n=3) || u16_le(i=1)   // valid ThresholdParams
    || 0x01                                        // Interpolation::Lagrange
    || 32-byte canonical encoding of scalar 0      // secret_share = 0 (accepted)
    || canonical encoding of G::identity()         // verification_shares[1]
    || canonical encoding of G::identity()         // verification_shares[2]
    || canonical encoding of G::identity()         // verification_shares[3]
```

`ThresholdKeys::read` succeeds: `ThresholdParams::new(2,3,1)` is valid, `read_F` accepts `0`, `read_G` accepts each canonical identity encoding, `verification_shares.len() == 3 == n`, and every participant index `<= n`. `group_key` is computed as `id·L_1(0) + id·L_2(0) = identity`. Result: `keys.group_key()` returns the identity point — a working "zero address" key that any party can sign for with secret `0`, while the victim treats it as their threshold key.

Variant (attacker-controlled key): pick a degree-1 polynomial `f` with `f(0) = a` known to the attacker; write `verification_shares[j] = G·f(j)` and `secret_share = f(1)`. `group_key() == G·a`, `view({1,2})` produces shares that pass verification and complete signatures under `G·a` — a key the attacker spends from alone.