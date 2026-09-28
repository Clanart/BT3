### Title
Unchecked u64 arithmetic on attacker-controlled payment amounts and fee rate overflows, producing a transaction that pays more than its inputs or burns all funds as fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` computes `payment_sat`, `needed_fee`, and the change value with plain `u64` `sum`/`+`/`*`/`-` operations over values that originate from externally supplied payment requests (`payments: &[(ScriptBuf, u64)]`) and `fee_per_vbyte`. In release builds these wrap silently (Rust only panics on overflow in debug), so the `NotEnoughFunds` sufficiency check can be bypassed and the resulting transaction either (a) declares outputs whose total exceeds the inputs — an invalid transaction that can never be broadcast — or (b) drops the change output and sends the entire input remainder to miners. This is the same class as the libXfont2 `BitmapScaleBitmaps` bug: an unchecked integer-width computation on an input-derived size/amount produces a corrupted buffer/value downstream — here, a corrupted transaction instead of a heap buffer.

### Finding Description
The vulnerable arithmetic is:

- `let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();` — `networks/bitcoin/src/wallet/send.rs:187`. Each `payment.1` is a `u64` only constrained by `*amount >= DUST` (line 165-169). Summing a handful of large amounts (e.g. two payments of `u64::MAX - 100`) wraps `payment_sat` to a small value.
- `let mut needed_fee = fee_per_vbyte * vbytes;` — `send.rs:206`, and `fee_per_vbyte * vbytes_with_change` — `send.rs:227`. A large `fee_per_vbyte` wraps `needed_fee` small.
- `if input_sat < (payment_sat + needed_fee)` — `send.rs:215`. `payment_sat + needed_fee` itself can wrap, so the solvency check passes even when the true output total vastly exceeds `input_sat`.
- `input_sat.checked_sub(payment_sat + fee_with_change)` — `send.rs:228`. If `payment_sat + fee_with_change` wraps below `input_sat`, `checked_sub` returns `Some`, and a change output of `value` is created while `payment_sat`'s real value was enormous — or the subtraction correctly fails and change is silently dropped, pushing the entire remainder into the fee (`fee()` at `send.rs:138-141` is `sum(inputs) - sum(outputs)` and will itself underflow-panic if outputs were allowed to exceed inputs).
- `input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>()` — `send.rs:175` — can also wrap if the prevout set totals over `u64::MAX` (unrealistic on-chain, but the code does not bound it).

The code correctly uses `checked_sub` in exactly one place (line 228) and `u64::try_from`/`u32::try_from` elsewhere, showing the pattern was known but not applied to the multiplications and additions.

### Impact Explanation
Payments reaching `SignableTransaction::new` are derived from user withdrawal requests routed through the scheduler; `payments` is the attacker-influenced input. Consequences:

1. **Invalid signed transaction / unspendable funds.** If `payment_sat` (or `payment_sat + needed_fee`) wraps below `input_sat`, the `NotEnoughFunds` check passes and the constructed `tx_outs` contain `Amount::from_sat(payment.1)` values summing to more than the inputs. All threshold signers then produce valid BIP-340 signatures for a transaction no Bitcoin node will ever accept — the multisig has signed an unintended, unspendable transaction and burned an attempt/nonce.
2. **Fee burn.** If `fee_per_vbyte * vbytes` wraps `needed_fee` to near zero while the real per-vbyte rate is large, `needed_fee < min relay` may still trip, but a wrap landing just above the minimum bypasses it while the change branch fails `checked_sub`, leaving `sum(inputs) - payment_sat` as the actual fee — potentially the entire wallet balance paid to miners.

This maps to the accepted impact of "funds reported/spent that are not spendable" and "signing of an unintended message" (the sighash commits to a malformed transaction the callers never intended).

### Likelihood Explanation
Medium. The trigger requires a plan with payment amounts whose `u64` sum overflows — i.e. the scheduler must accept payment amounts approaching `u64::MAX` without validating them against actual coin supply/available balance before constructing the transaction. The wraparound behavior occurs silently in release builds (no `overflow-checks`), so no panic guards it. Likelihood depends on upstream validation of payment amounts, which is not enforced in this crate.

### Recommendation
Replace the raw arithmetic with checked operations and explicit bounds:

- `send.rs:175,187`: accumulate with `try_fold`/`checked_add`, returning `NotEnoughFunds`/`TooLargeTransaction` on overflow.
- `send.rs:206,227`: `fee_per_vbyte.checked_mul(vbytes)` with an error on overflow.
- `send.rs:215`: `payment_sat.checked_add(needed_fee)` before comparing to `input_sat`.
- `send.rs:139`: compute `fee()` with `checked_sub` and surface an error rather than panicking.
- Reject individual payment amounts above the coin's `MAX_MONEY` (21M BTC) at the same place `DUST` is enforced.

### Proof of Concept
```rust
// networks/bitcoin — conceptual PoC against SignableTransaction::new
// Two payments each just under u64::MAX pass the >= DUST check.
let huge = u64::MAX - 1000;
let payments = vec![(script_a, huge), (script_b, huge)];
// payment_sat = (2*huge) mod 2^64 = small, e.g. ~2046
// input_sat (e.g. 1 BTC) >= payment_sat + needed_fee (small)  => check passes
let tx = SignableTransaction::new(inputs, &payments, Some(change), None, 1).unwrap();
// tx.transaction().output now contains two TxOuts of ~u64::MAX sats each:
// a transaction spending ~3.6e11 BTC from 1 BTC of inputs — invalid on the network,
// yet fully signed by TransactionSignMachine::sign via taproot_key_spend_signature_hash.
// Similarly, fee_per_vbyte = u64::MAX makes needed_fee wrap so the change-output
// checked_sub path silently drops change and the whole input becomes fee.
```

Note: I verified the arithmetic sites in `send.rs` directly. I could not fully trace how `fee_per_vbyte` and `payments` are populated upstream in the processor/scheduler (outside the listed in-scope paths), so the reachability claim assumes withdrawal-derived payments reach this constructor without a coin-supply bound, which `send.rs` itself does not enforce.