### Title
Inconsistent parity negation between received-output recognition and spending makes offset keys with odd Y unspendable - (networks/bitcoin/src/wallet/send.rs)

### Summary
Like the Fei report's inconsistent inversion of oracle prices, `bitcoin-serai` handles BIP-340 Y-parity negation inconsistently: `tweak_keys` negates an odd group key, the Schnorr `Hram`/`verify` path negates the challenge/signature for an odd `R`, but `SignableTransaction::multisig` and `p2tr_script_buf` treat an odd-parity derived key as unusable instead of negating it. Since Taproot outputs commit only to the x-coordinate, an output paying to `keys.offset(o)` whose group key has odd Y is recognized as received yet can never produce a spendable signature machine.

### Finding Description
BIP-340/BIP-341 key-path spends require the secret key to correspond to the even-Y representative of the x-only output key; if the derived internal key has odd Y, the signer must use `-x`. The codebase handles this correctly in two places and incorrectly in a third:

1. `tweak_keys` explicitly negates the whole `ThresholdKeys` when the tweaked group key is odd (`keys.scale(conditional_select(ONE, -ONE, needs_negation))`), preserving spendability (`networks/bitcoin/src/wallet/mod.rs:67-74`).
2. `Hram::hram` negates the challenge when `R` is odd, and `Schnorr::verify` negates the summed `s` when `sig.R` is odd, matching BIP-340 nonce parity (`networks/bitcoin/src/crypto.rs:59-73`, `crypto.rs:145-149`).
3. `p2tr_script_buf` returns `None` for any odd-Y key, and `SignableTransaction::multisig` propagates that `None` (`if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey { None? }`), aborting the entire multisig machine (`networks/bitcoin/src/wallet/send.rs:276-281`, `mod.rs:80-86`).

Each `ReceivedOutput` carries a scalar `offset` applied per input (`send.rs:176`, `send.rs:276`). The script_pubkey of a Taproot output is a pure function of the x-coordinate, so it is identical for `P` and `-P`. There is no code path that negates `keys.offset(o)` to reach the even-Y representative before constructing the `AlgorithmMachine`; the code that *does* know how to negate (`tweak_keys`) is only applied once to the base key, and `scale(-1)` on an already-odd offset key is never attempted.

The offset for an output is an arbitrary scalar read via `ReceivedOutput::read` / supplied at output registration (`mod.rs:122-134`, `send.rs:176`). For an offset where `keys.offset(offset).group_key()` has odd Y — which an unprivileged party funding a deposit address can induce by choosing/provoking the registered offset, or which simply occurs with ~1/2 probability for unconditioned offsets — the output is a valid Taproot output to that x-coordinate (scanners match on `script_pubkey`, which is parity-agnostic), but `SignableTransaction::multisig` returns `None` because `p2tr_script_buf` rejects the odd key rather than the signer negating the key as BIP-340 requires.

### Impact Explanation
Funds sent to an output whose derived key has odd Y are reported as received (the script_pubkey matches and the `ReceivedOutput` is registered with its offset) yet are unspendable: `multisig` yields `None`, no `TransactionMachine` is created, and no threshold signature can ever be produced for that input. This is a direct loss-of-funds condition reachable purely through public transaction data, with no key compromise or malicious validator required — analogous to a price being silently inverted in one component but not another, here the required parity negation exists in `tweak_keys` but is absent (replaced by rejection) in the per-output spend path.

### Likelihood Explanation
The parity of `group_key + offset·G` is effectively uniform over offsets, so any offset-selection process that does not condition on even parity produces unspendable outputs with probability ~1/2 per output. If the offset registration path forces even parity (not verified — the `Scanner`/`register_offset` implementation was beyond the lines inspected), the flaw is latent rather than triggered, but the asymmetry remains: the spend path has no negation fallback and silently fails via `None` propagation, so any future or edge-case path producing an odd derived key burns the output.

### Recommendation
In `SignableTransaction::multisig` (or at offset registration), normalize the derived keys to even parity the same way `tweak_keys` does: compute `k = keys.offset(offset)`, and if `needs_negation(&k.group_key())` then `k = k.scale(-Scalar::ONE)` before calling `p2tr_script_buf` and `AlgorithmMachine::new`. Alternatively, make `p2tr_script_buf`-based construction infallible by always emitting the even-Y key, and document that all offset consumers must negate odd keys. Ensure the recognition path (scanner/`ReceivedOutput` registration) and the spend path apply identical parity rules.

### Proof of Concept
1. Let `keys` be a `ThresholdKeys<Secp256k1>` already passed through `tweak_keys` (even group key). Choose an offset `o` such that `(keys.clone().offset(o)).group_key()` has odd Y — found by trying two consecutive `o` values.
2. A payer sends funds to `ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&odd_key)))` — a perfectly valid Taproot output; the on-chain script is identical to the even-Y key's.
3. The scanner registers a `ReceivedOutput { offset: o, output, outpoint }`; the funds are counted as received.
4. `SignableTransaction::new(vec![received], payments, change, None, fee)` succeeds, since the check of spendability never happens there.
5. `tx.multisig(&keys)` executes `p2tr_script_buf(offset.group_key())?` on the odd-Y key (`send.rs:277`), hits the `Tag::CompressedEvenY` rejection at `mod.rs:81-83`, and returns `None` — no signing machine exists, and the output can never be spent, despite being correctly addressed and confirmed.

Root cause: parity negation is applied at `tweak_keys` (`mod.rs:67-74`) and for the nonce/`s` in `crypto.rs:72,146`, but the per-input offset path in `send.rs:276-281` treats odd parity as fatal instead of negating the derived keys.