### Title
`Schnorr` claims BIP-340 compatibility but omits BIP-340's public-key parity rule, producing unverifiable signatures for odd group keys - (File: networks/bitcoin/src/crypto.rs)

### Summary
`networks/bitcoin/src/crypto.rs` documents its `Hram`/`Schnorr` implementation as "A BIP-340 compatible HRAm" and "BIP-340 Schnorr signature algorithm," yet it only implements BIP-340's *nonce* parity rule (negating the challenge/`s` when `R` is odd). BIP-340 additionally requires the *secret key* to correspond to the even-Y representative of the x-only public key; when the group key `A` has odd Y, every signature this code produces fails BIP-340 verification against `x(A)`. Compatibility is only restored by an external convention (`tweak_keys` negating odd keys), which the algorithm itself neither enforces nor documents as a precondition.

### Finding Description
BIP-340 defines verification over x-only keys: the verifier implicitly uses `P = even-Y lift of x(A)`, i.e. `P = -A` when `A` is odd. A valid signature therefore satisfies `s·G = R_even + c·P_even` with `c = tagged_hash("BIP0340/challenge", x(R) || x(A) || m)`.

Serai's implementation (`Hram::hram`, `Schnorr::verify`/`sign_share`):

- computes `c` correctly over x-coordinates (`crypto.rs:59-73`),
- negates `c` and later `s` when `R` is odd (`crypto.rs:72`, `crypto.rs:146`), which is algebraically equivalent to BIP-340's nonce negation,
- but signs with `params.secret_share()` directly (`algorithm.rs:208-210`), i.e. the discrete log of `A` itself, never the discrete log of `P_even`.

Resulting check: `s·G = -R + c·A`, while BIP-340 requires `s·G = -R + c·P_even`. These agree iff `A` is even-Y; for an odd-Y group key the emitted `(x(R), s)` is invalid under every conforming BIP-340 verifier — including Bitcoin consensus for Taproot key-path spends.

The only guard is `tweak_keys` (`wallet/mod.rs:46-75`), which negates odd keys *as an application-level convention on the base group key*. Nothing in the `Algorithm`/`Hram` API enforces evenness, and keys used for signing can acquire odd parity through other mechanisms (e.g., `ThresholdKeys::offset`, which the scanner/spend path uses per received output — `processor/src/networks/bitcoin.rs:120-122` shows output keys reconstructed as `script_key - offset·G`, i.e., effective spend keys are offset-adjusted and their parity is not tied to the tweaked base key's parity). This is a partial-iteration caveat: I could not fully confirm whether the `SignableTransaction` path re-negates offset-adjusted keys before signing; if it does not, the bug is directly reachable on-chain.

### Impact Explanation
Any FROST signing session whose effective group key is odd-Y produces a signature that is correctly formed under Serai's internal verification (`SchnorrSignature::verify` passes, since it checks `sG = R + cA` internally) but is rejected by BIP-340/Taproot consensus. Funds locked to such a key are unspendable via key-path, and the defect is silent: nothing fails locally, the failure only appears when the transaction is broadcast. Because parity is ~50/50, a code path that reaches `Schnorr` with an unnegated key fails deterministically half the time.

### Likelihood Explanation
The bug triggers whenever signing occurs under a group key whose evenness wasn't established by `tweak_keys` — most plausibly keys composed with non-tweak offsets. If the wallet's per-output offset flow does not re-establish even parity (unverified within this analysis), any user deposit to an offset address producing an odd effective key creates an unspendable output — matching the "funds reported received that are not spendable" criterion. If `tweak_keys`/offset handling does re-negate everywhere today, this remains a latent violation of the advertised BIP-340 interface: the type is named and documented as BIP-340 while silently requiring a precondition external tools and integrators have no way to discover from the API.

### Recommendation
In `Hram::hram` (or the `Schnorr` algorithm), fold in the key-parity rule: negate `c` (equivalently, sign with the negated secret share) when `A` is odd, matching BIP-340's `d'` adjustment. Apply the same parity handling to the effective key after any `offset`/`scale` composition in `networks/bitcoin/src/wallet`, or assert evenness of `params.group_key()` in `sign_share` so misuse fails loudly instead of emitting an unspendable signature.

### Proof of Concept
Conceptual trace with an odd-Y group key `A` (e.g., obtained via `ThresholdKeys::offset` on a tweaked base key):

1. Nonce sum `R` even. `c = tagged_hash("BIP0340/challenge", x(R) ‖ x(A) ‖ m)`; `needs_negation(R) = false`, so `c` is used as-is.
2. `sign_share` returns `r_i + c·x_i`; summed `s = r + c·x` where `A = x·G`.
3. `verify` returns `(x(R), s)` unchanged (`crypto.rs:145-149`).
4. A BIP-340 verifier computes `P = -A` (even lift of `x(A)`) and checks `s·G = R + c·(-A) = R - cA`, but `s·G = R + cA`. Verification fails; a Taproot spend carrying this signature is rejected by consensus.

Serai's own `verify` accepts the signature (it checks against `A`, not the even lift), so the invalidity is undetectable without an external BIP-340 verifier — the same test-hole shape as the test in `networks/bitcoin/src/tests/crypto.rs`, which only ever exercises a `tweak_keys`-normalized (even) key.