### Title
`SignableTransaction::new` Panics on u64 Overflow When Summing Attacker-Controlled Input Values - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The audit finding is an arithmetic-underflow DoS: an amount was computed by subtracting a baseline measured on the wrong account, so an unprivileged party could inflate one side of the subtraction and make a public claim path always revert. The Serai analog is the unchecked `u64` summation of `ReceivedOutput` values in `SignableTransaction::new`. `ReceivedOutput` is deserializable from untrusted bytes (`ReceivedOutput::read`, `networks/bitcoin/src/wallet/mod.rs:122-134`), and its embedded `TxOut.value` is a raw consensus-decoded `u64` that is never range-checked against Bitcoin's 21M-coin supply. Summing attacker-controlled `Amount` values in `input_sat` (or in `fee()`) overflows `u64` and panics (or wraps in release, producing incorrect funds accounting), DoSing transaction construction and corrupting the `NotEnoughFunds` / change / fee math built on it.

### Finding Description
`SignableTransaction::new` computes:

```rust
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

at `send.rs:175`, and `fee()` computes `sum(prevouts) - sum(outputs)` at `send.rs:139-141`. Both assume the total satoshis fit in `u64`, which is only guaranteed for *real* chain outputs. Nothing enforces this on deserialized `ReceivedOutput`s: `ReceivedOutput::read` (`wallet/mod.rs:122-134`) consensus-decodes an arbitrary `TxOut`, so an untrusted peer feeding bytes into `read` can set `value` to e.g. `u64::MAX`. Two such inputs overflow `input_sat` and panic in debug builds; in release builds `sum` wraps, making `input_sat` tiny, which then mis-drives `NotEnoughFunds { inputs, ... }` (send.rs:215-221) and the change computation `input_sat.checked_sub(payment_sat + fee_with_change)` (send.rs:228), so change is silently dropped and/or a valid transaction fails construction. Like the original bug — where a balance difference taken against the wrong baseline made an attacker-inflated value underflow the reward claim — an attacker who controls one side of the sum/subtraction forces the arithmetic to fail for honest users.

### Impact Explanation
Transaction construction (`SignableTransaction::new`) is the entry point to every spend. A panic or wrapped `input_sat` permanently blocks or mis-funds transaction building for any session whose input list includes attacker-supplied `ReceivedOutput`s — mirroring the audited finding's permanent revert of `claimRewards`. Additionally, `fee()`'s `sum(inputs) - sum(outputs)` (`send.rs:139-141`) will panic if wrapped output sums exceed wrapped input sums, and incorrect `needed_fee`/`NotEnoughFunds` reporting leaks into callers.

### Likelihood Explanation
`ReceivedOutput::read` is a public deserialization API intended for untrusted bytes (it is listed among reachable read paths), and no validation caps `TxOut.value` to the valid satoshi range (< 21e14). Any flow where inputs are received over the wire — rather than produced locally by `Scanner::scan_transaction` — is exposed. The malicious party needs no keys or validator status, only to supply crafted serialized outputs.

### Recommendation
Validate `output.value` in `ReceivedOutput::read` (reject values above `bitcoin::Amount::MAX_MONEY`, 21_000_000 * 100_000_000), mirroring Bitcoin Core's `MoneyRange` check on deserialization. Additionally, use `checked_add`/`checked_sub` for `input_sat`, `payment_sat`, and `fee()` and surface overflow as a `TransactionError` rather than panicking.

### Proof of Concept
Serialize two `ReceivedOutput`s via `ReceivedOutput::read` bytes where each embedded `TxOut.value` is `0xFFFFFFFFFFFFFFFF`. Call `SignableTransaction::new(inputs, &[(some_script, 1000)], None, None, 1)` — `input_sat` overflows `u64` and panics (`attempt to add with overflow`), or in release mode wraps to `u64::MAX - 1` vs the real total, producing a bogus `NotEnoughFunds`/change result. A single input with a huge value similarly makes `fee()`'s subtraction (`send.rs:139`) underflow once outputs are subtracted.