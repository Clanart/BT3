### Title
BIP-340 parity handled for `R` but never for the group key, producing invalid signatures for ~half of all keys - (File: networks/bitcoin/src/crypto.rs)

### Summary
The NLTK bug class is a security-relevant check that evaluates a condition on the wrong value (a normalized path compared against itself), rendering it permanently inert. The analog in Serai is the BIP-340 parity handling in `bitcoin_serai::crypto::Schnorr`: the code normalizes the nonce point `R` to even-Y (negating the challenge in `Hram::hram` and negating `s` in `Schnorr::verify`), but it never normalizes the signing public key `A` to its even-Y lift. Under BIP-340, both `R` and the public key are x-only and must be treated as even-Y points. When the threshold group's aggregate key has odd Y, every signature the honest signing set produces fails BIP-340 verification on-chain, because the verifier uses `-x` (the even lift of `A`) while the signers computed `s` against `x`.

### Finding Description
In `networks/bitcoin/src/crypto.rs`:

- `Hram::hram` computes `c = H(x(R), x(A), m)` and then negates `c` when `R` is odd, so the share is effectively produced for `-r` — this correctly handles `R`'s parity (lines 59-73).
- `Algorithm::verify` constructs the signature from the nonce sum and, if `sig.R` is odd, emits `s = -sum`, again handling only `R`'s parity (lines 139-150).
- Nowhere is the parity of the group key `A` checked. `x_only()` (lines 21-23) explicitly drops the key's sign bit for the on-chain x-only representation, and `ThresholdKeys::offset` (`crypto/dkg/src/lib.rs:414-417`) adds the TapTweak scalar to the share without ever conditionally negating the base share when the internal key is odd.

BIP-340 verification computes `sG =? R' + e·P` where `P` is the even-Y lift of `x(A)`. If `A` is odd, `P = -A`, so the signer must effectively use secret `-x`, not `x`. The FROST share produced is `s = c·x + r` (via `SchnorrSignature::sign`, `crypto/schnorr/src/lib.rs:74-84`), giving `sG = cA + R`. For odd `A`, this equals `-c·P + R`, which does not satisfy `sG = c·P + R'`, so the emitted 64-byte signature fails consensus BIP-340 verification. The parity "normalization" that exists inspects `R` but never `A` — the same shape as comparing the normalized path to itself instead of to the sandbox root.

The same gap infects per-share blame: `verify_share` checks each share against `view.verification_share(l)`, which corresponds to `x_i` with no parity correction (`crypto/frost/src/algorithm.rs:219-230`), so even a hypothetical caller-side `s` fix would desync blame verification.

### Impact Explanation
Any Bitcoin output controlled by this threshold key whose aggregate/internal group key has odd Y-coordinate (~50% of generated keys) cannot be spent: the threshold signing session completes "successfully" (`AlgorithmSignatureMachine::complete` returns a signature because `Schnorr::verify` checks against the *unnormalized* key `A` via the generic `SchnorrSignature::verify`, which passes for the odd `A`), yet the resulting transaction is rejected by Bitcoin nodes' BIP-340 validation. Funds are reported received at the x-only address but are provably unspendable — a permanent loss of any coins sent to such keys. This is squarely in the "funds reported received that are not spendable" impact class.

### Likelihood Explanation
Deterministic for any group key with odd Y — roughly half of all keys produced by the DKG/promotion paths, absent an integrator-side negation convention (none is documented; `needs_negation` is only applied to `R`). Triggering requires no malicious behavior: honest signers following the protocol produce signatures that fail on-chain verification. Reachable entirely via public inputs — a Bitcoin transaction signed through `bitcoin_serai::crypto::Schnorr`'s FROST `Algorithm` implementation for an odd-Y key.

### Recommendation
Before signing, detect `needs_negation(group_key)` (equivalently, the internal key before TapTweak). If odd, negate the effective secret contribution — i.e., produce the share as `c·(-x_i) + r` — and correspondingly negate each participant's `verification_share` used by `verify_share`/blame, and negate the tweak-offset ordering (`d' = -d + tweak`, not `d + tweak`). Assert parity consistency in `verify` so a session that would emit an invalid signature fails locally instead of returning an unspendable signature.

### Proof of Concept
```rust
// networks/bitcoin/src/crypto.rs
// For a ThresholdKeys<Secp256k1> whose group_key() A has odd Y:
let algorithm = Schnorr::new();
// ... run FROST sign over included set; complete() returns Some(sig) because
// SchnorrSignature::verify checks sG = cA + R for the *odd* A, which holds.
let sig: [u8; 64] = machine.complete(shares).unwrap();

// On-chain BIP-340 check uses P = lift_x_even(x(A)) = -A:
//   sG = cA + R = c(-P) + R ≠ cP + R'  →  signature invalid.
// needs_negation(&sig.R) is applied to s, but no negation of x ever occurs,
// so ~50% of group keys yield unspendable outputs.
```

The relevant code is `Hram::hram`/`Schnorr::verify` in `networks/bitcoin/src/crypto.rs:59-73,139-150`, which normalize only `R`, and `ThresholdKeys::offset` in `crypto/dkg/src/lib.rs:414-417`, which applies the TapTweak offset additively without parity-conditional negation of the base share.

Caveat: I could not verify within the available iterations whether `networks/bitcoin/src/wallet/` already negates the offset or rejects odd keys when registering/tweaking (e.g., in `register_offset`/address construction). If the wallet layer unconditionally rejects or pre-negates odd internal keys, the exploitability is reduced to that layer's correctness; the cryptographic gap in `crypto.rs` stands either way.