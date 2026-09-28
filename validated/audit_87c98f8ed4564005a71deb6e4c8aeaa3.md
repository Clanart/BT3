### Title
Type-confused `Interpolation` discriminant in `ThresholdKeys::read` lets attacker-supplied bytes redefine the key's interpolation semantics and weights - ([File: crypto/dkg/src/lib.rs])

### Summary
`SIccCalcOp::ArgsPushed()` treats an object read from attacker-controlled input as a different object type than intended. The Serai analog is the untagged→tagged deserialization in `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:604-616`): a single attacker-controlled byte selects whether the key material is interpreted as `Interpolation::Constant(Vec<F>)` or `Interpolation::Lagrange`, and under `Constant` the attacker additionally supplies the raw per-participant interpolation weights (`c[i-1]` in `interpolation_factor`, `crypto/dkg/src/lib.rs:226-249`). Because `ThresholdKeys::new` never verifies that `secret_share * G == verification_shares[i]`, a fully attacker-crafted serialized `ThresholdKeys` produces signing state inconsistent with any honestly-generated key.

### Finding Description
`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reconstructs a `ThresholdKeys` from a byte stream. After the curve ID and `(t, n, i)` header, it reads a one-byte discriminant:

- `0` → `Interpolation::Constant` followed by `n` raw `C::F` scalars that become the interpolation weights.
- `1` → `Interpolation::Lagrange`.

This is the same shape as the iccDEV bug: a tag in untrusted input selects which logical type the following bytes are interpreted as. With `Constant`, `interpolation_factor` returns `c[u16::from(i) - 1]` (`crypto/dkg/src/lib.rs:228`), i.e., an arbitrary attacker-chosen weight per participant, replacing the mathematically-determined Lagrange coefficient. `ThresholdKeys::view` (`crypto/dkg/src/lib.rs:463-533`) then multiplies both the local `secret_share` and each verification share by these weights, and `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) computes `group_key` as `Σ verification_shares[i] * weight_i` — all derived from attacker-provided scalars and points.

Crucially, nowhere in `ThresholdKeys::new`/`read` is the deserialized `secret_share` checked against `verification_shares[i]` (compare with PedPoP, which does verify shares via `share_verification_statements` in `crypto/dkg/pedpop/src/lib.rs:430-449`). The deserialized structure is trusted wholesale once parsed.

### Impact Explanation
Any flow that loads a `ThresholdKeys` from attacker-influenced bytes (key backup/restore, share transfer, or a crafted blob substituted for an honestly generated key file) yields keys where:

- The effective secret share used during FROST signing is `secret_share * c[i-1] * scalar` with attacker-chosen `c`, so the participant emits signature shares under weights/keys it never agreed to — signing shares bound to an attacker-chosen group key rather than the DKG'd key.
- `group_key()` (`crypto/dkg/src/lib.rs:445-447`) resolves to a key inconsistent with the rest of the validator set, so funds addressed to it are unspendable and produced shares fail peer verification.

This maps to the "incorrect verifier formula / signing of unintended message" acceptance class: the deserialized object is of a different logical type than the DKG produced, and the cryptographic invariants assumed by callers (`share_i * G == verification_share_i`, Lagrange-determined weights) do not hold.

### Likelihood Explanation
Likelihood is moderate-to-low: exploitation requires the attacker to control or replace the serialized `ThresholdKeys` bytes a participant loads (e.g., a restored backup or imported share package), which is a narrower surface than a network message. No cryptographic break of an honestly generated key is possible — the honest DKG output path (`calculate_share` → `ThresholdKeys::new` with `Interpolation::Lagrange`) is unaffected. Rated Medium.

### Recommendation
- On deserialization, verify `secret_share * C::generator() == verification_shares[i]` inside `ThresholdKeys::new` (or at least in `read`), rejecting self-inconsistent key material.
- Restrict `Interpolation::Constant` construction to internal/trusted callers, or reject `Constant` in `ThresholdKeys::read` unless the caller explicitly opts into it, since serialization round-trips of DKG output only ever produce `Lagrange`.
- Optionally hash the interpolation discriminant and coefficients into the context used by downstream signing so a type-confused blob cannot silently pass.

### Proof of Concept
Conceptual: craft a byte stream `id_len || C::ID || t || n || i || 0x00 || c[0..n] || secret_share || verification_shares[1..=n]` where `c` are arbitrary scalars and `verification_shares`/`secret_share` are unrelated. `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574`) accepts it because `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349`) only checks `t == n` for the `Constant` case and share count, never share consistency. Subsequent `view(included)` calls `interpolation_factor` (`crypto/dkg/src/lib.rs:228`) returning `c[i-1]`, so `secret_share()` is scaled by the attacker's constant and `group_key()` is the attacker's weighted sum — the participant signs under attacker-defined weights with a share not matching any honestly-derived verification share.

Note: severity depends on whether an unprivileged party can actually influence the bytes passed to `ThresholdKeys::read` in the deployed flow; the deserialization itself is confirmed attacker-parseable per the in-scope rules, but the persistence/loading path was not fully traceable within the indexed code.