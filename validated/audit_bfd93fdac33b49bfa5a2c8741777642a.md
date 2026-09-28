### Title
BIP-340 signature produced for odd-Y group keys is invalid on-chain, making received funds unspendable - (File: networks/bitcoin/src/crypto.rs)

### Summary
Analogous to the stored-XSS report — where content stored under a trusted origin executes with authority it should not have — `frost_crypto::Schnorr::verify` emits a signature that is accepted internally (the FROST equation `s·G = R + c·A` holds) but is invalid under the authority that actually matters, the BIP-340 consensus verifier. Specifically, the code only corrects for the parity of the nonce point `R` and never accounts for the parity of the group key `A`, even though BIP-340 implicitly uses the even-Y representative of `x(A)`.

### Finding Description
`Hram::hram` computes `c = H_BIP340(x(R), x(A), m)` and negates `c` when `R` has odd Y, so each participant signs `r_i − c·x_i` instead of `r_i + c·x_i` (`networks/bitcoin/src/crypto.rs:59-73`). `Schnorr::verify` then negates the aggregated scalar `s` when `R` is odd, producing `s' = −r + c·x` (`networks/bitcoin/src/crypto.rs:145-149`).

A BIP-340 verifier uses `P = ` even-Y point with `x(P) = x(A)`, i.e. `P = A` if `A` is even and `P = −A` if `A` is odd. Checking the emitted signature:

- `A` even, `R` even: `sG = R + cA` ✓
- `A` even, `R` odd: `s'G = −R + cA`; verifier computes `R' = s'G − cA = −R` (even, correct x) ✓
- `A` odd, `R` even: verifier expects `sG = R + c(−A) = R − cA`, but `sG = R + cA` ✗
- `A` odd, `R` odd: verifier expects `s'G = −R − cA`, but `s'G = −R + cA` ✗

Whenever the group key (or the taproot-tweaked key, since the `ThresholdView` additive offset shifts `A` to `A + tG` with uncontrolled parity) has an odd Y coordinate, `verify` returns `Some(sig)` for a signature that fails consensus validation, because the internal check `self.0.verify(group_key, nonces, sum)` verifies the FROST equation against the odd-Y `A` itself and never reconstructs what the chain will compute.

### Impact Explanation
The parity of a freshly generated threshold key is effectively random, so roughly half of all key sets (or tweaked output keys) produce signatures that Bitcoin consensus rejects. The signing pipeline reports success and the funds sitting at the corresponding script_pubkey cannot be spent by that signature — funds reported received that are not spendable. No collusion or malicious participant is required.

### Likelihood Explanation
Deterministic whenever `y(A)` (or `y(A + tG)`) is odd — a ~50% probability per key/tweak. Reachable purely through normal operation; an unprivileged depositor triggering a spend of such an output hits it.

### Recommendation
Normalize the effective key inside `sign_share`/`verify`: when `A` (the key whose x-coordinate the challenge binds) is odd, the share equation must use `−x_i` contributions — e.g. negate `c`'s key term or negate `s` conditioned on `needs_negation(A)` — so that the emitted signature satisfies `sG = R_even + c·A_even`. Equivalently, fold key-parity normalization into the `ThresholdView` offset mechanism so `sign_share` always signs against the even-Y representative of the x-only key.

### Proof of Concept
```rust
// With a Secp256k1 ThresholdKeys whose group_key A has odd Y:
let keys: ThresholdKeys<Secp256k1> = ...; // assert!(bool::from(needs_negation(&keys.group_key())));
let machine = AlgorithmMachine::new(bitcoin_schnorr::Schnorr::new(), keys);
// run preprocess/sign/complete honestly for any msg
let sig: [u8; 64] = machine.complete(shares).unwrap();
// On-chain BIP-340 check with P = x_only(A) (even-Y):
//   c' = H("BIP0340/challenge", sig[0..32] || x(A) || msg)
//   R' = sG - c'P  !=  the R the signers committed to
// bitcoin::secp256k1 verification fails; verify() still returned Some(sig).
```
Internally `SchnorrSignature { R, s }.verify(A, c)` passes because it checks against the odd-Y `A`, while consensus checks against `−A`, so the mismatch is never detected before the signature is released.

Confidence note: I verified the negation math in `networks/bitcoin/src/crypto.rs` (`hram` lines 59–73, `verify` lines 145–149). I could not confirm whether key generation elsewhere forces `group_key` to even-Y before this code path runs; if the DKG guarantees even-Y keys and all tweaks preserve it, this degrades to a non-issue, but nothing in `crypto.rs` enforces that invariant.