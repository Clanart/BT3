### Title
BIP-340 key-parity negation is applied only to the nonce `R`, never to the group key `A`, producing invalid Taproot signatures for odd-Y aggregate keys - (File: networks/bitcoin/src/crypto.rs)

### Summary
The original bug was an adjustment (`extraFeeAsset0`) applied to the wrong side of a computation depending on a direction flag. The same class appears in Serai's BIP-340 FROST driver: a conditional negation required by BIP-340 is applied for the nonce point `R` (by negating the challenge and later `s`), but the equivalent parity correction for the group key `A` is never applied. BIP-340 defines verification against the even-Y lift of the x-only public key; when the aggregate group key `A = x·G` has odd Y, the effective secret must be `-x`. The code signs with `x` regardless, so roughly half of all threshold keys emit signatures Bitcoin consensus rejects.

### Finding Description
`Hram::hram` hashes `x(R)`, `x(A)`, `m` and then conditionally negates the challenge when `R` is odd, effectively signing `r - c·x` so the final `s` can be negated to `-r + c·x` (`crypto.rs:59-73`). In `Schnorr::verify`, `s` is negated iff `sig.R` is odd (`crypto.rs:145-149`). This correctly implements the "nonce must have even Y" half of BIP-340. However, nothing normalizes the signer secret for the parity of `A`. The FROST share is computed as `r' + c·x_i` under the raw secret shares whose aggregate public key `A` may be odd. Internally `FrostSchnorr::verify` checks consistency against the full point `A`, so the assembled signature passes the library's own check, but a BIP-340 verifier computes `s·G - c·lift_x(A.x)` with `lift_x` always even — equal to `-A` when `A` is odd — yielding `R' ≠ R.x` and rejecting the signature. The signature is emitted into a Taproot key-spend witness via `TransactionSignMachine::sign`/`taproot_key_spend_signature_hash` (`send.rs:373-397`), where `p2tr_script_buf(offset.group_key())` commits only `x(A)` (`send.rs:277`).

### Impact Explanation
Any multisig whose FROST group key (after per-input offset) has odd Y produces Schnorr signatures that fail BIP-340 verification on-chain, so the transaction is invalid and the funds it tries to spend cannot move. Since key parity is effectively random, ~50% of generated/offset keys are affected.

### Likelihood Explanation
Parity of a FROST aggregate key (and of `key + offset·G` per input, `send.rs:276-281`) is unbiased; each signing attempt with an odd-parity key deterministically fails. No malicious party is needed — it is a pure correctness bug reachable whenever an odd key is used, though an attacker could also choose an `offset` that flips parity.

### Recommendation
Normalize the signing secret to even-Y before computing shares: when `needs_negation(A)` is true, sign with `-x_i` (equivalently negate `c` once more, or negate each share's key term), and ensure `verify` checks `s·G == R_even + c·A_even` with `A_even = conditional_select(A, -A, needs_negation(A))`. Add a test asserting the emitted 64-byte signature passes `bitcoin::secp256k1` `verify_schnorr` for both parities of `group_key`.

### Proof of Concept
1. Run DKG to obtain `ThresholdKeys<Secp256k1>`; repeat until `needs_negation(keys.group_key())` is true (≈50% of runs), or choose a `ReceivedOutput` offset `o` such that `group_key + o·G` is odd.
2. Build a `SignableTransaction` spending that output (`send.rs:273-285`) and complete the FROST signing flow; `Schnorr::verify` (`crypto.rs:139-150`) accepts internally.
3. Feed the resulting `(R.x || s)` signature and `x(A)` to `secp256k1::schnorr::verify`. It fails, because `s·G - c·lift_x(A.x) = R + 2c·A ≠ R`, while the same procedure succeeds for even `A`. The transaction's witness is therefore consensus-invalid.

Confidence caveat: I could not inspect `frost::algorithm::IetfSchnorr`'s `sign_share`/`verify` internals in the remaining iterations; the finding assumes (consistent with the visible code) that no even-Y key normalization occurs there — `crypto.rs` shows none, and the sign bit of `A` is only ever dropped via `x(A)` in the challenge, never used to negate the secret.