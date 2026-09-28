### Title
BIP-340 signatures produced without normalizing an odd-Y group key are always invalid - (File: networks/bitcoin/src/crypto.rs)

### Summary
The Bitcoin `Schnorr` `Algorithm` implementation handles BIP-340's even-Y requirement for the nonce `R` (via `needs_negation` on `R` in `Hram::hram` and the `s`-negation in `verify`), but never normalizes the group/public key `A`. Whenever the FROST group key (or the scaled/offset key actually being signed under) has an odd Y coordinate, every signature produced is unconditionally rejected by BIP-340 verification — an always-incorrect control-flow path reachable purely through data the signer set derives and broadcasts.

### Finding Description
`Hram::hram` hashes `x(R)`, `x(A)` and the message, and negates the challenge when `R` is odd so the final `s` can be negated to "move" `R` to its even representative (`crypto.rs` lines 59–73). In `verify` (lines 139–150), after the inner `FrostSchnorr::verify` succeeds, the code conditionally negates `sum` based on `needs_negation(&sig.R)` and emits `R.x || s`.

The missing branch is the equivalent normalization for `A`. BIP-340 verification lifts `x(A)` to the even-Y point `A_even`. The FROST signers, however, compute shares against `params.group_key()` — the raw `ThresholdView::group_key()` (`crypto/frost/src/algorithm.rs` line 208, `crypto/dkg/src/lib.rs` lines 377–378), which has ~50% probability of being odd-Y. The combined equation is

```
s·G = R ± c·A        (A = actual group key, possibly odd)
```

while any BIP-340 verifier checks

```
s·G = R_even + e·A_even,  e = H(x(R) || x(A) || m)
```

When `A` is odd (`A = -A_even`), the produced signature satisfies `R_even + e·A` and therefore fails the verifier equation `R_even + e·(-A)` — deterministically, for every signing attempt under that key. The internal `self.0.verify(...)` call passes (it uses the same `self.c` and the same `A`), so `complete()` in `crypto/frost/src/sign.rs` (lines 465–466) returns `Ok`, no blame is assigned, and the invalid signature is emitted as `[u8; 64]` without any fallback path.

There is no negation of the group key (or equivalently of the challenge term / secret share) anywhere in this `Algorithm` impl: `needs_negation` is only ever applied to `R`, never to `group_key`, and `sign_share` (lines 128–136) forwards `params.secret_share()` unmodified.

### Impact Explanation
For any threshold Bitcoin wallet whose group key — including the result of `ThresholdKeys::scale`/`offset` used for tweaks (`crypto/dkg/src/lib.rs` lines 400–417) — has odd Y, all signatures the FROST protocol produces are invalid at the Bitcoin consensus layer. Funds locked to `x(A)` become unspendable through this signing path even though every participant behaved honestly and `complete()` reported success. This is the analog of CVE-2020-1914's "always-incorrect" semantics: a code path that is structurally reachable and unconditionally produces wrong output.

### Likelihood Explanation
Parity is effectively a coin flip per key: roughly half of all generated/tweaked group keys are odd-Y. No attacker action is required beyond normal protocol participation (or simply a wallet whose key lands on odd parity); the failure is deterministic once the key parity is fixed.

### Recommendation
Normalize the key used for signing: in `sign_share`/`verify`, compute `needs_negation(&params.group_key())` and negate `params.secret_share()` (and correspondingly the per-signer verification share in `verify_share`) when `A` is odd, so the signature satisfies `s·G = R_even + e·A_even`. Alternatively, negate the challenge path consistently and document that callers must provide an even-Y `ThresholdKeys`. Add a regression test asserting produced signatures pass `XOnlyPublicKey` BIP-340 verification for both parities of the group key.

### Proof of Concept
1. Run FROST key generation for `Secp256k1` producing a `ThresholdKeys` whose `group_key()` has odd Y (`needs_negation(&group_key)` returns 1; retry generation until so — ~50% of keys).
2. Run the standard `AlgorithmMachine::new(Schnorr::new(), keys)` preprocess/sign/complete flow over `t` participants for any `msg`.
3. `complete()` returns `Ok([u8; 64])`.
4. Verify the signature with a BIP-340 verifier: `schnorr_verify(sig, x_only(&group_key), msg)` returns false — deterministically, regardless of participant honesty or retries.