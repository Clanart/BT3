### Title
Missing identity (zero-element) check on `verification_shares` allows forged FROST signature shares - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes the per-participant `verification_shares` map — the analogue of the report's `yieldTrackers` array — using `Ciphersuite::read_G`, which only enforces canonical encodings and does **not** reject the identity point (`crypto/ciphersuite/src/lib.rs:91-101`). `ThresholdKeys::new` then validates the map's length and participant indexes but never checks `is_identity` on any share (`crypto/dkg/src/lib.rs:355-365`). An identity point is therefore accepted as a participant's verification share, exactly the "zero address in the array" bug class.

### Finding Description
`Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) decodes a point and checks `point.to_bytes() == encoding` for canonicity, but identity is a canonical encoding (e.g., 32 zero bytes for Ristretto), so it passes. Only `Curve::read_G` in FROST (`crypto/frost/src/curve/mod.rs:125-131`) rejects identity — and `ThresholdKeys::read` at `crypto/dkg/src/lib.rs:620-623` uses the non-rejecting `C::read_G`. The resulting `verification_shares` map can contain `C::G::identity()`.

In `view()` (`crypto/dkg/src/lib.rs:500-521`), each share is scaled by scalar and interpolation factor, keeping it identity. During FROST signature verification of a share, the check for participant `i` is effectively `s_i·G = R_i + c·λ_i·V_i`. With `V_i = identity`, this degenerates to `s_i·G = R_i`, so the holder of that slot can produce a valid signature share knowing only their own nonce — no secret share needed. Additionally, `group_key` in `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:376-378`) is derived by summing the interpolated shares, so injected identity shares silently alter/corrupt the group key.

### Impact Explanation
An attacker who can feed crafted bytes to `ThresholdKeys::read` (an explicitly in-scope untrusted-byte sink) can install a participant slot with an identity verification share. Whoever signs as that participant produces shares that verify under the degenerate equation `s_i·G = R_i`, forging signature shares without the corresponding secret share — or, if all crafted shares are attacker-chosen, defines an arbitrary group key. For `Interpolation::Constant` (MuSig path), this mirrors the MuSig `check_keys` gap: `musig` also never rejects identity keys (`crypto/dkg/musig/src/lib.rs:46-63`), so an identity entry in `keys` produces a slot whose holder needs no secret to pass share verification.

### Likelihood Explanation
Requires an attacker to supply the serialized `ThresholdKeys` bytes (or the `keys` array to `musig`/`musig_key_vartime`, which accept identity since `C::read_G`/`check_keys` do not filter it). Reachable wherever key material or participant key lists are deserialized from untrusted input; the code comments acknowledge the identity hazard elsewhere (`Curve::read_G` explicitly rejects it for FROST preprocess/commitment paths), indicating the omission in `ThresholdKeys::read`/`ThresholdKeys::new`/`check_keys` is an oversight rather than intent. Medium.

### Recommendation
In `ThresholdKeys::new`, reject any `verification_shares` value for which `is_identity()` holds. Similarly, add an identity check in `check_keys` (`crypto/dkg/musig/src/lib.rs`) for MuSig key lists, or switch `ThresholdKeys::read` and PedPoP `Commitments::read` to an identity-rejecting point reader equivalent to `Curve::read_G`.

### Proof of Concept
1. Serialize a `ThresholdKeys` blob per `crypto/dkg/src/lib.rs:574-632`: valid `C::ID`, `t`, `n`, `i`, interpolation byte `0`/`1`, a canonical secret share, then `n` point encodings where slot `j` is the identity encoding (32 zero bytes for Ristretto).
2. `ThresholdKeys::<Ristretto>::read` succeeds — `C::read_G` accepts identity, `ThresholdKeys::new` checks only length and indexes.
3. Call `view(included)` including participant `j`. Its interpolated verification share is `identity`.
4. When verifying `j`'s signature share, the equation reduces to `s_j·G = R_j`; set `s_j = d_j + e_j·ρ_j` (the nonce sum) — the share verifies without `j`'s secret share, i.e., a forged signature share.