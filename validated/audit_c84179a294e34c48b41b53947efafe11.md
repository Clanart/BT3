### Title
BIP-340 `Schnorr` algorithm normalizes `R` parity but never normalizes the group key parity, producing signatures invalid under BIP-340 for odd-Y group keys - (File: networks/bitcoin/src/crypto.rs)

### Summary
The Xen bug class is applying a construct designed for one domain (HVM physmap) to a different domain (PV) where its invariants do not hold. The Serai analog is in the Bitcoin BIP-340 wrapper around `IetfSchnorr`: it applies the BIP-340 "even-Y nonce" normalization (negating the challenge in `Hram::hram` and negating `s` in `verify`) while completely omitting the paired BIP-340 normalization for the public key — a BIP-340 verifier always verifies against the even-Y lift `lift_x(A.x)`. When the FROST group key has odd Y (which an unrestricted DKG/`ThresholdKeys` produces with ~50% probability and nothing in this code rejects or negates), the emitted signature passes the library's own internal `SchnorrSignature::verify` yet fails every real BIP-340/Taproot verification.

### Finding Description
`frost_crypto::Hram::hram` computes the tagged `BIP0340/challenge` hash over `x(R) || x(A) || m` and conditionally negates `c` when `R` is odd (`crypto.rs:59-73`). `Schnorr::verify` then runs the inner `FrostSchnorr::verify`, which checks `s·G == R + c·A` against the *actual* `group_key` `A` (`algorithm.rs:214-216`), and finally conditionally negates `s` when `sig.R` is odd (`crypto.rs:145-149`).

The flaw: negating `s` for odd `R` is only half of the BIP-340 parity semantics. For a BIP-340 verifier, the signature `(R.x, s')` is checked as `s'·G == R_even + e·lift_x(A.x)`, where `lift_x(A.x)` is the *even* representative — i.e. `-A` whenever the real `A` is odd. Serai's internal check, however, was performed against the un-negated odd `A`, so:

- `R` odd (the case the code handles): `s'G = -R - cA = R_even + e·A`. BIP-340 expects `R_even + e·(-A)`. These differ by `2e·A`, so the emitted 64-byte signature is invalid on-chain.
- `R` even, `A` odd: `s'G = R + e·A`, but BIP-340 expects `R + e·(-A)` — again invalid.

Only when `A` is already even does the output satisfy BIP-340. Nothing in `Hram`, `Schnorr::verify`, `sign_share`, or the `IetfTranscript` path detects, rejects, or compensates for an odd-parity `group_key`; `x(A)` in the challenge is parity-agnostic, so hashing alone does not bind the parity. The doc comments mention only the point-at-infinity panic condition — no parity precondition is documented or enforced (`crypto.rs:76-83`).

### Impact Explanation
If the aggregate FROST key used for a Bitcoin output has an odd-Y point (the natural outcome of key generation half the time unless an external layer negates the shares/key — which this crate neither does nor documents as a requirement), every threshold signature produced under it is rejected by Bitcoin consensus' BIP-340 verification. For Taproot key-path spends this renders the associated outputs unspendable via the group key — funds are controlled by a key that cannot produce a valid spend signature, causing loss of availability of funds and signing-session failure that blame machinery cannot attribute to any participant (each individual share verified correctly under `verify_share` with the same inconsistent `c`).

### Likelihood Explanation
Group-key parity is attacker-influenceable only indirectly (participants' DKG contributions shift the key), but no malicious action is needed: an honest DKG yields an odd-Y group key with probability ~1/2. Whenever the integrating code path (e.g. a processor's `ThresholdKeys` → `Schnorr::new()` signing flow) does not itself normalize the secret shares to the even-lift key, signing deterministically produces invalid signatures for that key. Reachability is via the ordinary `Algorithm::sign_share`/`verify` path on public messages; no special bytes are required.

### Recommendation
Normalize the group key parity inside this module rather than relying on callers: e.g., in `sign_share`/`verify`, detect `needs_negation(&group_key)` and, when set, negate the effective secret share/`group_key` used (or fold a key-parity bit into the challenge handling symmetric to the nonce handling), so the emitted `s` satisfies `s·G == R_even + e·lift_x(A.x)`. At minimum, `assert!` the group key is even-Y in `Schnorr::verify`/`sign_share` so a misconfigured view fails loudly instead of emitting an unspendable signature.

### Proof of Concept
1. Construct a `ThresholdKeys<Secp256k1>` (1-of-1 suffices: `group_key = x·G`) and reject sampled secrets until `needs_negation(&group_key)` is true (odd Y — expected 2 trials).
2. Build `frost_crypto::Schnorr::new()`, run the normal preprocess + `sign` flow over any `msg`; `Algorithm::verify` returns `Some(sig)` because the internal `SchnorrSignature::verify(group_key, c)` passes with the un-negated odd `A` (`crypto.rs:145`, `algorithm.rs:215-216`).
3. Feed the 64-byte `sig` to a reference BIP-340 verifier (e.g. `bitcoin::key::Secp256k1::verify_schnorr` against `XOnlyPublicKey` `x_only(&group_key)`). Verification fails: the verifier checks against `lift_x(A.x) = -A`, while the signature was generated consistent with `+A`. The same procedure with an even-Y `group_key` verifies, confirming the defect is the missing key-parity normalization.

Uncertainty note: I could not inspect `ThresholdKeys`/`ThresholdView` construction (`keys.rs` is absent from this snapshot — `ThresholdKeys`/`ThresholdView` are re-exported in `crypto.rs:44` but defined in `crypto/frost/src/lib.rs`, whose contents I did not retrieve). If an upstream caller unconditionally negates shares for odd group keys, the concrete trigger requires that normalization to be absent; the defect in this file — performing nonce-parity normalization while silently assuming key parity — stands regardless, and no parity precondition is documented or enforced here.