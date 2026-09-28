### Title
Unchecked u64 arithmetic on untrusted amounts bypasses the insufficient-funds check in `SignableTransaction::new` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The upstream bug is a signed-integer overflow triggered when a malformed `mempool.dat` supplies an attacker-chosen `i64` time field, which is then added to a constant (`time + 1209600`) without a bound check. The analogous pattern exists in `SignableTransaction::new`: payment amounts, input values, and fee quantities are `u64` values derived from untrusted inputs (`payments` and `Vec<ReceivedOutput>`, which has a `ReceivedOutput::read` deserialization path accepting arbitrary bytes at `networks/bitcoin/src/wallet/mod.rs:122-134`), and they are combined with unchecked `+`, `*`, and `sum()` operations. An overflow silently wraps (release) or panics (debug), bypassing the `NotEnoughFunds` guard and causing the threshold signer to produce a transaction the validation logic was meant to reject.

### Finding Description
`SignableTransaction::new` performs three unchecked arithmetic operations on attacker-influenced `u64` values:

- `networks/bitcoin/src/wallet/send.rs:175` — `input_sat` is a plain `sum::<u64>()` over `input.output.value.to_sat()` for each `ReceivedOutput`. `ReceivedOutput::read` (`wallet/mod.rs:122-134`) accepts a `TxOut` consensus-decoded from raw bytes, whose `value` can be any `u64`, so a crafted stream of outputs overflows `input_sat`.
- `networks/bitcoin/src/wallet/send.rs:187` — `payment_sat` is a plain `sum::<u64>()` over the caller-supplied payment amounts, which can be driven by a transaction the unprivileged party causes to be signed (e.g., a withdrawal of `u64::MAX` sats).
- `networks/bitcoin/src/wallet/send.rs:206` and `:227` — `fee_per_vbyte * vbytes` is an unchecked multiplication.
- `networks/bitcoin/src/wallet/send.rs:215` — the solvency guard `input_sat < (payment_sat + needed_fee)` uses unchecked addition. With `payment_sat` near `u64::MAX`, `payment_sat + needed_fee` wraps to a small value, so `input_sat < wrapped` is false and the `NotEnoughFunds` error is skipped.
- `networks/bitcoin/src/wallet/send.rs:228` — `input_sat.checked_sub(payment_sat + fee_with_change)` only guards the subtraction; the inner addition `payment_sat + fee_with_change` itself overflows first, wrapping to a small value so `checked_sub` returns `Some(~input_sat)`, pushing a change `TxOut` worth nearly the entire input sum.

Like the upstream `9223372036854775807 + 1209600` overflow, the deserialized/arithmetic-bound value is never range-checked before being used in a security-relevant comparison.

### Impact Explanation
In release builds the wrap causes `SignableTransaction::new` to construct and return a transaction whose declared outputs (`Amount::from_sat(payment.1)` at line 190) exceed the total input value — a transaction the `NotEnoughFunds` check exists to reject. `multisig()`/`sign()`/`complete()` then drive FROST to sign this malformed transaction and `fee()` (`send.rs:138-141`) returns a wrapped/garbage value, so the reported fee accounting is wrong. In debug builds the same inputs panic the process. Either way the multisig can be induced to sign (and broadcast) a consensus-invalid transaction or to compute an incorrect change output, and the solvency invariant enforced by `TransactionError::NotEnoughFunds` is void.

### Likelihood Explanation
Triggering requires a payment amount or a `ReceivedOutput` value near `u64::MAX` to reach `SignableTransaction::new`. Real on-chain outputs are bounded by Bitcoin's supply (~2.1e15 sats), so scanner-derived `ReceivedOutput`s cannot overflow `input_sat` on their own; however, `payments` amounts come from transaction data a party causes to be signed, and `ReceivedOutput::read` is an in-scope untrusted-byte path. Whether upstream integrators cap payment amounts before this call determines practical reachability — the code itself performs no such validation (`*amount < DUST` is the only amount check, `send.rs:165-169`).

### Recommendation
Use checked arithmetic throughout `SignableTransaction::new`: `try_fold`/`checked_add` for the `input_sat` and `payment_sat` sums, `checked_mul` for `fee_per_vbyte * vbytes`, and `checked_add` inside the solvency check and the `checked_sub` argument at line 228. Additionally bound payment amounts (e.g., reject `amount > MAX_MONEY`-style limits) before constructing `TxOut`s.

### Proof of Concept
```rust
// Requires the caller/integrator to pass an attacker-chosen payment amount,
// or untrusted ReceivedOutput bytes via ReceivedOutput::read.
let huge_payment = (script_pubkey, u64::MAX); // single payment of 2^64-1 sats
let inputs = vec![received_output_with_value(1_000_000)]; // honest 0.01 BTC input

// fee_per_vbyte * vbytes is small; payment_sat + needed_fee wraps:
// u64::MAX + needed_fee -> needed_fee - 1 (release build)
// => `input_sat < (payment_sat + needed_fee)` is `1_000_000 < tiny` == false
// => NotEnoughFunds check passes, SignableTransaction is built and signed
let tx = SignableTransaction::new(inputs, &[huge_payment], None, None, 1).unwrap();
// tx.tx.output[0].value == Amount::from_sat(u64::MAX) — consensus-invalid,
// yet signed by TransactionSignMachine::sign / complete.
```