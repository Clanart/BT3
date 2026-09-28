### Title
Unchecked `payment_sat + needed_fee` overflow lets an extreme payment amount (e.g. `u64::MAX`) bypass the `NotEnoughFunds` check and produce a signed yet unspendable/invalid Bitcoin transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` sums attacker-influenced payment amounts into `payment_sat` and then checks funds with `input_sat < (payment_sat + needed_fee)`. Both the `+` additions and the `fee_per_vbyte * vbytes` multiplication are plain unchecked arithmetic. Analogous to the reported bug where `type(uint256).max` is a reachable, unhandled extreme value, a payment of `u64::MAX` satoshis (obtainable by a user burning `u64::MAX` on-chain to request a withdrawal, or simply via overflow of the sum of multiple payments) causes `payment_sat + needed_fee` to wrap, defeating the solvency check.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`):

- `payment_sat` is a plain `sum::<u64>()` over the payment amounts (line 187).
- `needed_fee = fee_per_vbyte * vbytes` uses unchecked multiplication (lines 206, 227).
- The solvency check `if input_sat < (payment_sat + needed_fee)` uses unchecked addition (line 215).

In release builds (overflow checks off), `payment_sat + needed_fee` wraps past `u64::MAX`, so a `payment` carrying `u64::MAX` (or many payments whose sum exceeds `u64::MAX`) makes the comparison evaluate against a tiny wrapped value. The `NotEnoughFunds` error is skipped and a `TxOut { value: Amount::from_sat(u64::MAX), ... }` is committed into the transaction. The FROST threshold then signs (via `TransactionSignMachine::sign`, which commits to `Prevouts::All` and every output under `TapSighashType::Default`, lines 373-390) a transaction that is consensus-invalid: an output of ~1.8e19 sat exceeds the 21M BTC money supply and exceeds `input_sat`, so the signed transaction can never be broadcast, yet the inputs/prevouts were committed to.

### Impact Explanation
The threshold signing set produces signatures over a sighash committing to an impossible output. The resulting transaction is permanently unbroadcastable, so the requested withdrawal is bricked after the user's burn already succeeded on the Serai side — funds effectively locked, and a signing session consumed on garbage. If overflow checks are enabled, the same input instead panics inside the processor (DoS of the signing pipeline). Either way it is a reachable denial-of-service/funds-locking condition caused by an unhandled extreme value, mirroring the "max value always reverts" class.

### Likelihood Explanation
Payment amounts are not constrained to `<= 21_000_000 * 10^8` or to `input_sat` individually; only the post-addition comparison exists. An unprivileged user who burns a balance and specifies a withdrawal amount near `u64::MAX` (or multiple outputs whose sum wraps) triggers the wrap. The upstream burn path (`Coins::burn_with_instruction`) accepts `Amount(u64::MAX)`-scale values, so the input is publicly reachable.

### Recommendation
Use checked arithmetic throughout `SignableTransaction::new`: `payment_sat = payments.iter().try_fold(0u64, |a, p| a.checked_add(p.1))`, `fee_per_vbyte.checked_mul(vbytes)`, and `payment_sat.checked_add(needed_fee)`; error on overflow. Also validate each payment `<= MAX_MONEY` (21e14 sats) alongside the existing `DUST` lower bound.

### Proof of Concept
```rust
// inputs: a single ReceivedOutput worth 1_000_000 sats (well-funded key)
// payments: &[(some_script, u64::MAX)]
// change: None, data: None, fee_per_vbyte: 1
let payment_sat = u64::MAX;                 // sum over payments
let needed_fee = vbytes;                    // e.g. ~150
// payment_sat + needed_fee wraps to ~149 in release:
assert!(input_sat < payment_sat.wrapping_add(needed_fee) == false);
// NotEnoughFunds is skipped; tx_outs gains TxOut { value: u64::MAX }
// SignableTransaction::multisig(...) then signs Prevouts::All committing to
// an output greater than the total Bitcoin supply -> invalid, unspendable TX.
```

Relevant code: `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs:175-235` and the sighash commitment in `TransactionSignMachine::sign` at `networks/bitcoin/src/wallet/send.rs:373-390`.