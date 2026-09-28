### Title
Unchecked u64 arithmetic on attacker-influenced payment amounts overflows `payment_sat`, bypassing the `NotEnoughFunds` check and signing a consensus-invalid transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` sums payment amounts and input values with plain u64 arithmetic (`sum::<u64>()`, `payment_sat + needed_fee`). An unprivileged party who can cause the coordinator to build a withdrawal transaction controls the payment amounts; multiple large amounts overflow `payment_sat`, wrapping it to a small value so the `input_sat < (payment_sat + needed_fee)` check passes when it should fail.

### Finding Description
At `networks/bitcoin/src/wallet/send.rs`:

```rust
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
...
let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

- `payment_sat` is the sum of `payment.1: u64` values (line 187). Each payment amount originates from a `Payment`'s `balance.amount.0` passed in by `make_signable_transaction` in `processor/src/networks/bitcoin.rs:441`, i.e., externally supplied withdrawal amounts.
- `needed_fee = fee_per_vbyte * vbytes` (line 206) can also overflow the u64 multiply.
- The solvency check `if input_sat < (payment_sat + needed_fee)` (line 215) uses wrapping `+`. If `payment_sat` wraps (e.g., two payments of `u64::MAX/2 + k` each), the wrapped total is small, `input_sat < wrapped_total` is false, and `NotEnoughFunds` is never raised.
- The change path uses `input_sat.checked_sub(payment_sat + fee_with_change)` (line 228), which returns `None` with the wrapped sum, so no change output is added and the transaction is built anyway.
- The resulting `SignableTransaction` is fed to `TransactionSignMachine::sign`, which computes real BIP-341 sighashes and produces valid FROST signature shares over a transaction whose outputs exceed its inputs — a transaction Bitcoin consensus will always reject.

### Impact Explanation
The threshold multisig signs a transaction that can never confirm. This permanently consumes a signing session/attempt for outputs that cannot be spent, creating a denial of service against the bridge's withdrawal pipeline: the offending payment can be retried indefinitely (each attempt burns coordinator/multisig work) or the plan must be manually reconstructed to exclude the crafted amounts. Additionally, in debug builds the overflowing `+`/`*` panics, crashing the signer mid-operation.

### Likelihood Explanation
Any user able to submit a withdrawal/payment request with a large `u64` amount can trigger it. The overflow requires the payment sum to exceed `u64::MAX`, which needs only 2 payments near `u64::MAX` — there is no per-payment upper-bound check (only the `>= DUST` lower-bound at lines 165–169 and the `> 21M BTC` bound is never enforced). No collusion or privileged position is needed.

### Recommendation
Use `checked_add`/`saturating_add` when accumulating `payment_sat` and `input_sat`, reject any payment `> MAX_MONEY` (21,000,000 * 10^8 sats), and use `checked_mul` for `fee_per_vbyte * vbytes`, surfacing a `TransactionError` on overflow.

### Proof of Concept
```rust
// Attacker requests two payments whose amounts each pass `>= DUST`
// but whose sum wraps u64.
let payments = [
  (attacker_script.clone(), u64::MAX - 1000),
  (attacker_script,        u64::MAX - 1000),
];
// payment_sat wraps to ~u64::MAX - 2000 => still huge; use three or
// (u64::MAX - 100, 200) style splits so the wrapped sum is small:
let payments = [
  (s1.clone(), u64::MAX), // wraps sum
  (s2,         1000),     // payment_sat == 999 after wrap
];
// input_sat (real UTXOs, e.g. 1 BTC) is NOT < 999 + needed_fee?
// needed_fee small => check passes, NotEnoughFunds skipped.
let tx = SignableTransaction::new(inputs, &payments, None, None, fee_rate)?;
// multisig(...).preprocess/sign produces FROST shares over a tx whose
// single output value is u64::MAX sats — invalid on the Bitcoin network.
```
The multisig completes a full FROST signing of a transaction Bitcoin will reject as consensus-invalid, wasting the signing round and blocking the withdrawal queue.