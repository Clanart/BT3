### Title
Attacker-controlled `ThresholdKeys` bytes redefine the group key and mint fully self-consistent threshold signers - (File: crypto/dkg/src/lib.rs)

### Summary
The external bug class is "untrusted input treated as trusted program state": October CMS's asset manager accepted attacker-chosen paths/extensions and executed them as PHP. The Serai analog is `ThresholdKeys::read` in `crypto/dkg/src/lib.rs:574-632`, which reconstructs a full threshold key from untrusted bytes with only syntactic checks (canonical scalars/points, `t/n/i` bounds). It performs no semantic validation: the `secret_share` is never checked against `verification_shares[i]`, the `Constant` interpolation coefficient vector is fully attacker-chosen, and `verification_shares` are read via `Ciphersuite::read_G` (which does not reject identity; only `Curve::read_G` at `crypto/frost/src/curve/mod.rs:125-131` does). The resulting `group_key` is computed purely from attacker-provided values (`verification_shares[i] * interpolation_factor(i)` at `crypto/dkg/src/lib.rs:376-378`), where for `Interpolation::Constant` the factors are the raw supplied scalars (`crypto/dkg/src/lib.rs:228`).

### Finding Description
`ThresholdKeys::read` deserializes:
1. curve ID (checked),
2. `t`, `n`, `i` (bounds-checked by `ThresholdParams::new`),
3. an interpolation tag: `0` reads `n` arbitrary scalars as `Constant(Vec<F>)`, `1` selects `Lagrange` (`crypto/dkg/src/lib.rs:604-616`),
4. an arbitrary `secret_share` scalar (`crypto/dkg/src/lib.rs:618`),
5. `n` arbitrary verification-share points (`crypto/dkg/src/lib.rs:620-623`).

`ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) then computes `group_key = Σ_{i=1..=t} verification_shares[i] * interpolation_factor(i, 1..=t)` and accepts the bundle without verifying that `secret_share * G` equals `verification_shares[i]`, that the interpolation factors are the legitimate MuSig binding factors, or that shares are non-identity. The FROST layer itself acknowledges this hole: `complete()` in `crypto/frost/src/sign.rs:491-494` emits `InternalError("everyone had a valid share yet the signature was still invalid")` precisely for "deserialize a semantically invalid FrostKeys" — a code path reachable only because deserialization admits invalid keys.

A crafted encoding can therefore instantiate a `ThresholdKeys`/`ThresholdView` where `group_key` is any point the attacker chooses (e.g., `s*G` for attacker-known `s`): set `secret_share = s`, `verification_shares[i] = s*G`, `Constant` factors `c_1 = 1, c_i = 0` (i>1). All downstream machinery — `view()` interpolation (`crypto/dkg/src/lib.rs:494-521`), `sign_share`, share verification via `verify_share`/`batch_statements` (`crypto/frost/src/algorithm.rs:219-230`), and `complete()` — operates consistently on the forged identity, producing signatures valid under `s*G`.

### Impact Explanation
Whoever feeds `ThresholdKeys::read` bytes decides what the "threshold group key" is. An attacker who can deliver key material (backup import, key-sync, any path where serialized `ThresholdKeys` are accepted rather than produced by the DKG) silently swaps the threshold identity for a single-key entity they fully control — the exact shape of the CMS compromise (untrusted asset input executed as privileged code). From then on every `sign`/`complete` yields signatures for the attacker's key: threshold security collapses to the attacker alone, and any funds/authorizations gated on `group_key` (e.g., the Bitcoin P2TR taproot output built from `group_key` via `tweak_keys`/`p2tr_script_buf` in `networks/bitcoin/src/wallet/mod.rs:80-86`) become spendable by the attacker without any honest signer's cooperation.

### Likelihood Explanation
Exploitation requires an integrator-level path delivering attacker bytes into `ThresholdKeys::read` — `GeneratedKeysDb::read_keys` (`processor/src/key_gen.rs:47-62`) sources from local DB, so a pure on-chain attacker cannot reach it directly; the exposure exists wherever serialized keys are imported or restored from external storage/backups. Given the directness of the primitive (one crafted byte string → complete key substitution, no algebraic work needed beyond picking `s`), and that the format carries a curve-ID check implying it's a loadable artifact, likelihood is moderate conditional on such an import path existing.

### Recommendation
`ThresholdKeys::read`/`new` should establish semantic integrity: verify `C::generator() * secret_share == verification_shares[i]` for the local participant, reject identity verification shares, validate the `Constant` coefficient vector length, and — since `Constant` factors are protocol-derived (MuSig binding factors) — recompute or authenticate them rather than trusting the encoding. Ideally serialization should embed/derive a commitment (e.g., hash over `group_key`) so tampered material fails early instead of producing a consistent but attacker-defined key.

### Proof of Concept
```rust
// crypto/dkg semantics, Serai crate versions as vendored.
// Attacker crafts bytes for ThresholdKeys::<C>::read:
let s = C::F::random(&mut OsRng);            // attacker-chosen secret
let id_len = u32::try_from(C::ID.len()).unwrap().to_le_bytes();
let mut buf = id_len.to_vec();
buf.extend(C::ID);
buf.extend(2u16.to_le_bytes());              // t = 2
buf.extend(2u16.to_le_bytes());              // n = 2
buf.extend(1u16.to_le_bytes());              // i = 1
buf.push(0u8);                               // Interpolation::Constant
buf.extend(C::F::ONE.to_repr().as_ref());    // c_1 = 1
buf.extend(C::F::ZERO.to_repr().as_ref());   // c_2 = 0
buf.extend(s.to_repr().as_ref());            // secret_share = s
for _ in 0 .. 2 {
  buf.extend((C::generator() * s).to_bytes().as_ref()); // vs_i = s*G
}
let keys = ThresholdKeys::<C>::read::<&[u8]>(&mut buf.as_ref()).unwrap();
// keys.group_key() == C::generator() * s  ->  attacker knows dlog.
// AlgorithmMachine::new(.., keys).sign/complete emits valid sigs for s*G
// using only the crafted share — no other participant needed.
```

Caveat: severity is contingent on an integrator path that accepts serialized `ThresholdKeys` from an untrusted party; within the pinned in-scope code, all observed call sites read from the node's own database. If no such ingestion path is considered in scope, this reduces to a hardening gap rather than a remotely reachable flaw.