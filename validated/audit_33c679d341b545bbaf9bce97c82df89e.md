### Title
BIP-340 signatures produced for odd-Y group keys are invalid on-chain — key-parity bit never accounted for - (File: networks/bitcoin/src/crypto.rs)

### Summary
`frost_crypto::Schnorr`, the algorithm used to produce BIP-340/Taproot signatures for Bitcoin outputs, handles the parity ("sign") bit of the nonce commitment `R` but never handles the parity bit of the group public key `A`. Under BIP-340 the verifier reconstructs the public key as the *even-Y* lift of `x(A)`, which is `-A` whenever `A` has odd Y. The signers must correspondingly sign as if their combined secret were `-x`. Because the code only negates the challenge and `s` for the nonce parity, any group key with odd Y (~50% of keys, and ~50% of tweaked keys since `offset()`/`TapTweak` rerandomizes parity) yields a 64-byte signature that fails BIP-340 verification — outputs become unspendable.

### Finding Description
The bug class from CVE-2016-4480 — a flag/level bit (the page-size bit) being ignored where it changes semantics — maps onto the Y-parity bit of the group key in the BIP-340 Schnorr algorithm.

`Hram::hram` computes `c = H(x(R) || x(A) || m)` and negates `c` when `R` is odd-Y, so the final `s` can be negated to match the even-Y `R` (`crypto.rs:59-73`). `Schnorr::verify` then conditionally negates `s` based on `needs_negation(&sig.R)` (`crypto.rs:145-149`). This correctly handles the parity of `R`.

However, `x(A)` is hashed while `A` may be odd-Y. In `sign_share`, shares are computed as `s_i = nonce_i + c * share_i` against `params.group_key()` (via `FrostSchnorr::sign_share`, crypto/frost/src/algorithm.rs:201-211) with no negation of the secret contribution when `A` is odd-Y. The internal `SchnorrSignature::verify(group_key, c)` check in `verify` passes because it verifies against the full point `A`, but the emitted signature `[x(R) || s]` is later checked by Bitcoin consensus against `A' = lift_x(x(A))` which equals `-A` for odd `A`. The equation `s*G = R + c*A'` fails since the signers actually computed `r + c*x` for `A = x*G` (odd), i.e. `s*G = R + c*A = R - c*A'`.

There is no compensating negation anywhere: `verify` only inspects `needs_negation(&sig.R)`, and the `ThresholdKeys` `offset()`/tweak mechanism used for TapTweak expresses additive tweaks, not the parity-dependent secret negation BIP-340 requires.

### Impact Explanation
Whenever the FROST group key (or the tweaked key being signed for — the offset changes parity pseudorandomly) has an odd Y coordinate, the produced 64-byte signature fails BIP-340 verification. Any Bitcoin output controlled by that key cannot be spent via the key path: the transaction is rejected by the network. This is an unprivileged-reachable failure — no attacker action is even needed; ~half of generated/tweaked keys are affected. Where an attacker can influence the tweak (e.g., the TapTweak hash parity), they can selectively cause signing sessions to produce invalid signatures, a permanent liveness/funds-availability failure for the affected outputs.

### Likelihood Explanation
Deterministic: for every signing session whose effective group key is odd-Y (probability ~1/2 per key/tweak), the emitted signature is invalid. All participants behave honestly; the flaw is purely in the parity handling of the algorithm wrapper.

### Recommendation
Mirror the nonce-parity handling for the key parity: when `needs_negation(&params.group_key())` is set, `sign_share` must use `-share` (equivalently negate `s` one additional time at `verify` and have `verify_share` evaluate against the negated verification share). Concretely, in `frost_crypto::Schnorr`, compute the key-parity flag in `sign_share`, apply it to the returned share, and apply the same correction in `verify`/`verify_share` so blame still works.

### Proof of Concept
Conceptually:

```rust
// Choose any ThresholdKeys<Secp256k1> whose group_key() has odd Y
// (sample keys until needs_negation(&keys.group_key()) == 1 — ~50% of keys).
let keys = key_gen_while(|k| bool::from(needs_negation(&k[&p1].group_key())));
let machines = algorithm_machines(&mut rng, &bitcoin_schnorr::Schnorr::new(), &keys);
let sig: [u8; 64] = sign(&mut rng, &Schnorr::new(), keys.clone(), machines, &sighash);

// Bitcoin-side check:
let xonly = XOnlyPublicKey::from_slice(&x(&group_key)).unwrap(); // lifts to -group_key
assert!(SECP256K1.verify_schnorr(&sig, &sighash, &xonly).is_err()); // FAILS
```

`sign_share` produces `s_i = r_i + c*x_i` and `verify` emits `s` (negated only if `R` odd). Bitcoin verifies `s*G == R + c*lift_x(x(A)) = R + c*(-A)`, which fails since `s*G = R + c*A`. The same session with an even-Y `A` verifies correctly, confirming the defect is exactly the unhandled key-parity bit, analog to the unhandled PS bit in `guest_walk_tables`.