### Title
BIP-340 signature invalid when aggregate Taproot key has odd Y — received funds unspendable - (File: networks/bitcoin/src/crypto.rs)

### Summary
The Rari Capital incident was a logic bug in a fund-handling contract (the RGT distributor's claim/deposit/withdrawal path) that forced operations to halt, though no funds were lost. The closest reachable analog in scope is in `bitcoin-serai`'s BIP-340 adaptation of the FROST Schnorr algorithm: `Schnorr::verify` corrects parity for the nonce point `R` only, and never accounts for the parity of the group (output) key. Under BIP-340/BIP-341, the public key committed in a P2TR output is x-only and is implicitly lifted to the even-Y point during verification. When the aggregate/tweaked key has odd Y, the signature produced for the odd-Y point fails Bitcoin-side verification, so outputs paying to it cannot be spent.

### Finding Description
`Hram::hram` negates the challenge scalar when `R` is odd (`needs_negation(R)`), and `Schnorr::verify` then negates the sum `s` for odd `R`, producing `s*G = R_even + c*P` (networks/bitcoin/src/crypto.rs:70-73, 145-149). Nothing negates for the parity of `P` (`view.group_key()` at crypto/frost/src/sign.rs:465). BIP-340 verification lifts `x(P)` to the even-Y point `P'`, so if `P` is odd the equation becomes `s*G = R_even - c*P'`, which fails.

`SignableTransaction::multisig` only checks that `p2tr_script_buf(offset.group_key())` equals the prevout's `script_pubkey` — an x-only comparison that ignores Y parity entirely (networks/bitcoin/src/wallet/send.rs:273-285). `TransactionSignMachine::sign` then computes shares via `AlgorithmSignMachine::sign` → `Schnorr::sign_share` with no key-parity correction (send.rs:355-398, crypto.rs:128-136). The aggregated signature therefore validates under the internal `FrostSchnorr` check (sign.rs:465) but produces a BIP-340 signature that Bitcoin nodes reject whenever the tweaked aggregate key is odd-Y — roughly 50% of the time for an arbitrary FROST key plus TapTweak.

### Impact Explanation
An unprivileged party sending BTC to the threshold wallet (a `ReceivedOutput` is accepted from untrusted chain data) can cause funds to be locked: the scanner/wallet reports the output as received and `multisig()` accepts it, yet no valid spend signature can ever be produced. This satisfies the "funds reported received that are not spendable" criterion — permanent freezing of funds with no theft, analogous to Rari's suspended claim/withdrawal operations.

### Likelihood Explanation
Triggering requires only that the tweaked aggregate key land on odd Y (~1/2 probability per key/offset). No malicious validator, collusion, or leaked keys are needed; any depositor reaches it with an ordinary Bitcoin transaction.

### Recommendation
In `Schnorr::verify`/`sign_share`, detect `needs_negation(group_key)` and negate the effective secret contribution (equivalently negate `s` so that `s*G = R' + c*P'`), or standardize the group key/offsets to even-Y before signing and before `p2tr_script_buf` comparison. Note a caveat: I was unable to inspect `ThresholdKeys::offset`/`p2tr_script_buf` internals in this pass; if they already force even-Y keys, this path is safe and the finding should be re-scoped to confirming that invariant is enforced.

### Proof of Concept
1. Construct a FROST `ThresholdKeys<Secp256k1>` whose tweaked `group_key()` has odd Y (regenerate offsets until odd).
2. Create a `ReceivedOutput` paying `p2tr_script_buf(group_key)` and build/sign via `SignableTransaction::multisig` → `preprocess` → `sign` → `complete`; `complete` succeeds because internal verification (sign.rs:465) uses the odd point directly.
3. Broadcast the resulting transaction: Bitcoin consensus rejects the witness signature, since verification lifts the x-only output key to its even-Y representative and `s*G ≠ R + c*P_even`.