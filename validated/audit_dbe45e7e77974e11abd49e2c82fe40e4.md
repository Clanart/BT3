### Title
Unauthenticated threshold-key creation: `ThresholdKeys::read` accepts arbitrary, inconsistent key sets (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes a complete threshold key set — threshold params, interpolation coefficients, the secret share, and all verification shares — purely from attacker-supplied bytes, and `ThresholdKeys::new` never checks that the secret share actually corresponds to the verification share for `params.i()` or that the shares form a coherent DKG output. The group key is derived solely from the attacker-chosen verification shares (`ThresholdKeys::new`, `crypto/dkg/src/lib.rs:376-378`). This is the Serai analog of unauthenticated "team" creation: an unauthenticated byte stream instantiates a new signing group that the rest of the system treats as authoritative.

### Finding Description
- `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reads `t`, `n`, `i`, an interpolation variant (Constant reads `n` raw scalars via `C::read_F`), a raw `secret_share` scalar, and `n` raw group elements as `verification_shares`. Every field is attacker-controlled.
- `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) validates only counts and index bounds; it computes `group_key` by interpolating the first `t` verification shares (lines 376-378) and never verifies `C::generator() * secret_share == verification_shares[params.i()]` or any other consistency relation.
- The downstream code itself acknowledges this reachable state: `AlgorithmSignatureMachine::complete` in `crypto/frost/src/sign.rs:492-494` has an `InternalError` branch for "deserialize a semantically invalid FrostKeys" — i.e., the FROST layer knows read-produced keys can be incoherent but does not prevent their creation or use.
- Because the group key is a pure function of the verification shares, an attacker can set the first `t` shares so the interpolated `group_key` equals a public key under attacker control, or a key for which no valid quorum of shares exists, while the stored `secret_share` is unrelated.

### Impact Explanation
Two concrete impacts, both reachable from untrusted bytes fed to `ThresholdKeys::read`:

1. **Funds reported received that are not spendable**: if the resulting `group_key` is used to construct a `Scanner` (`networks/bitcoin/src/wallet/mod.rs:162-166`), deposits sent to that address are reported as received outputs, yet no valid set of secret shares exists for the group key — the funds are permanently unspendable.
2. **Forged signing authority**: if the attacker constructs verification shares interpolating to their own public key and a consistent `secret_share`, the deserialized key set signs on behalf of an attacker-controlled group key, producing valid Schnorr/FROST signatures under a key the validator set never agreed to.

### Likelihood Explanation
Exploitation requires an attacker to influence the serialized `ThresholdKeys` blob consumed by a validator (e.g., restored state, a supplied key package, or any path passing untrusted bytes to `ThresholdKeys::read` — explicitly in-scope per the analog rules). Within that precondition, no proof, signature, or consistency relation is required: the "team" is created unconditionally. This mirrors the CVE's "unauthenticated creation" shape. Severity is Medium: the cryptographic primitives themselves are not broken, and the reachability depends on where serialized keys originate.

### Recommendation
In `ThresholdKeys::new` (or immediately after `read`), verify `C::generator() * secret_share == verification_shares[params.i()]` and reject keys failing the check. Where keys are restored from untrusted storage, additionally authenticate the serialized blob (e.g., MAC/signature or hash chained to on-chain state) before deserialization.

### Proof of Concept
1. Choose an attacker secret `a`; set attacker-controlled verification shares so that Lagrange interpolation of shares for participants `1..=t` yields `C::generator() * a` (e.g., pick `verification_shares[1..=t]` as any points summing/interpolating to that value, e.g., `A, 0, ..., 0` style constant-interpolation equivalents or solve the linear system for Lagrange).
2. Write a `ThresholdKeys` blob: valid `t, n, i`, `Interpolation::Lagrange`, an arbitrary `secret_share` scalar, and the chosen `n` verification shares.
3. Feed the blob to `ThresholdKeys::read`. It returns `Ok` with `group_key() = G*a`.
4. Construct `Scanner::new(keys.group_key())` — deposits to it are reported by `scan_transaction`/`scan_block` as `ReceivedOutput`s but no quorum can ever produce a valid signature; alternatively, if the attacker also sets a consistent `secret_share`, `AlgorithmMachine`/`SignMachine::sign` will emit shares under the attacker's key without any validator having participated in a DKG for it.