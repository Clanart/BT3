### Title
Unchecked u64 addition overflow in payment/fee summation allows crafted payment lists to panic or bypass the insufficient-funds check — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
JLSEC-2026-899 describes arithmetic producing a value "outside the range of representable values of type 'unsigned long'" when parsing untrusted input, causing undefined behavior / loss of availability. The direct analog in Serai is unchecked `u64` addition on attacker-influenced payment amounts in `SignableTransaction::new` in the in-scope `networks/bitcoin/src/wallet` code. The sums `payment_sat + needed_fee` (line 215) and `payment_sat + fee_with_change` (line 228, evaluated *inside* `checked_sub`'s argument) can exceed `u64::MAX`, panicking in debug builds or wrapping in release builds.

### Finding Description
`SignableTransaction::new` aggregates payment amounts and fees with plain `u64` arithmetic:

- Line 187: `let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();` — a crafted `payments` slice whose amounts sum past `u64::MAX` overflows inside `sum`.
- Line 215: `if input_sat < (payment_sat + needed_fee)` — even when `payment_sat` itself doesn't overflow, `payment_sat + needed_fee` can. Because `input_sat` is bounded by real Bitcoin supply (~2.1×10^15 sat), a wrapped `payment_sat + needed_fee` that lands below `input_sat` bypasses `TransactionError::NotEnoughFunds`.
- Line 228: `input_sat.checked_sub(payment_sat + fee_with_change)` — the inner addition is evaluated *before* `checked_sub`, so the overflow panic is not prevented by the checked subtraction.

Each payment amount is only required to be `>= DUST` (line 165–169); there is no upper bound or cumulative-overflow check. Callers constructing `SignableTransaction` from user-initiated burn/withdrawal instructions (e.g., `processor/src/networks/bitcoin.rs`, which builds `BSignableTransaction`s from `OutInstruction` payments) can pass amounts influenced by an unprivileged user submitting burn requests.

### Impact Explanation
- **Availability**: in any debug or `overflow-checks = on` build, the overflow panics inside `SignableTransaction::new`, crashing the signing task handling the transaction.
- **Release-mode wraparound**: if wrapping occurs, the `NotEnoughFunds` guard is bypassed and a `SignableTransaction` is produced whose output sum exceeds its inputs. The resulting transaction is consensus-invalid (outputs can't be funded), yet the FROST threshold will still produce signatures for it via `TransactionSignMachine::sign` (lines 355–398). The signed transaction can never confirm, permanently failing that signing attempt/eventuality — a liveness failure on a threshold-signed output. Additionally, `fee()` (lines 138–141) performs `sum(prevouts) - sum(outputs)` with unchecked subtraction, which underflows for such a transaction, causing a further panic.

This maps to the advisory's class: an out-of-range value produced during processing of untrusted numeric input, yielding undefined/wrapped behavior and loss of availability.

### Likelihood Explanation
An unprivileged party who can trigger a Bitcoin burn/withdrawal with chosen amounts supplies the `payments` amounts. Two payments near `u64::MAX/2` (or many moderately large payments) suffice to trigger the overflow. The amounts are `u64` and not validated against the 21M-BTC supply cap in this function, so no special privilege is needed beyond the ability to request a payment. Exploitation for the wraparound path additionally requires `wrapped_sum < input_sat`, which is satisfiable since `input_sat` is the sum of the multisig's real UTXOs.

### Recommendation
- Replace `sum::<u64>()` on payments with a `checked_add` accumulation returning `TransactionError`.
- Compute `input_sat.checked_sub(payment_sat).and_then(|v| v.checked_sub(needed_fee))` and likewise for `fee_with_change` before line 228.
- Cap each payment amount to `Amount::MAX_MONEY` (21M BTC in sats), since no valid Bitcoin output can exceed it.
- Make `fee()` use `checked_sub`/`saturating_sub` or assert the invariant, rather than unchecked `-` on line 139–140.

### Proof of Concept
```rust
// networks/bitcoin crate, wallet::SignableTransaction::new
// Given any valid ReceivedOutput `input` (e.g., value = 100_000 sats):

let payments = vec![
  (p2tr_script_buf(key).unwrap(), u64::MAX - 1000), // >= DUST, passes the dust check
  (p2tr_script_buf(key).unwrap(), u64::MAX - 1000),
];

// Debug build (or release with overflow-checks): panics on
// `payment_sat + needed_fee` at send.rs:215 (and possibly inside `sum` at :187).
// Release build without overflow checks: the sum wraps to a small value,
// `input_sat < wrapped` is false, NotEnoughFunds is skipped, and an
// unspendable transaction (outputs >> inputs) is produced and later signed
// by TransactionSignMachine::sign. Calling `.fee()` then underflows at
// send.rs:139-140.
let res = SignableTransaction::new(vec![input], &payments, None, None, 20);
```

Note: severity is bounded to Medium — the result is a panic/liveness failure of a signing attempt rather than key compromise, since the produced transaction is consensus-invalid and its signatures are only valid for that invalid sighash.