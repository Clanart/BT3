### Title
Unchecked u64 arithmetic in `SignableTransaction::new` lets wrapped payment/fee sums bypass the `NotEnoughFunds` check and produce consensus-invalid or under-funded transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the reported `DSWarp.warp` issue — an unguarded arithmetic operation on externally influenced values wrapping to an arbitrary value that destabilizes a core computation — `SignableTransaction::new` computes `input_sat`, `payment_sat`, `needed_fee`, `fee_with_change`, and the change amount with plain `sum`, `*`, and `+` on `u64` values that partially derive from untrusted input (`payments` amounts, `fee_per_vbyte`, `ReceivedOutput::read`-deserialized prevout values). An overflow wraps the solvency check to a small value, causing the multisig to sign a transaction that can never confirm.

### Finding Description
`SignableTransaction::new` performs all economic arithmetic on `u64` without checked operations:

- `input_sat` is an unchecked sum over attacker-influenceable prevout values: `inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>()` (send.rs:175).
- `payment_sat` is an unchecked sum over caller-supplied payment amounts: `payments.iter().map(|payment| payment.1).sum::<u64>()` (send.rs:187).
- `needed_fee` is an unchecked multiplication of caller-supplied `fee_per_vbyte` by `vbytes`: `let mut needed_fee = fee_per_vbyte * vbytes;` (send.rs:206), and likewise `fee_with_change = fee_per_vbyte * vbytes_with_change` (send.rs:227).
- The solvency gate itself adds wrapped values: `if input_sat < (payment_sat + needed_fee)` (send.rs:215).

`payment_sat` overflows with as few as two payments whose amounts sum past `u64::MAX` (e.g., two payments of `u64::MAX / 2 + 1`). After wrapping, `payment_sat + needed_fee` becomes a small number, the `NotEnoughFunds` error at send.rs:215-221 is bypassed, and `tx_outs` (built at send.rs:188-191 from the individual un-wrapped `payment.1` values) contains outputs summing to far more than `input_sat`. In builds with overflow checks enabled the same inputs panic instead — a remote DoS on the signer.

### Impact Explanation
The transaction constructed is consensus-invalid (outputs exceed inputs), so it can never be relayed or confirmed. Every participant that runs `sign`/`complete` on this `SignableTransaction` produces a valid-looking FROST signature over a sighash committing to unspendable outputs (sighash commits to `Prevouts::All` and outputs). The funds targeted by the plan are locked in a dead-end spend attempt: the inputs are not spendable through this transaction, required fee economics are corrupted, and the change calculation (`input_sat.checked_sub(payment_sat + fee_with_change)`, send.rs:228) is silently skipped when it wraps to `None`, potentially dropping change that should exist. Equivalently, a wrapped `needed_fee` near zero produces a real but un-relayable transaction. This is the direct analog of the report's "set a value to anything via overflow to corrupt a downstream computation" — here the destabilized quantity is the transaction's solvency invariant rather than `era()`.

### Likelihood Explanation
Reachable by an unprivileged party that can cause payment amounts, fee rates, or crafted `ReceivedOutput` values (via `ReceivedOutput::read` on untrusted bytes) to reach `SignableTransaction::new`. The amounts are free-form `u64`s and only `DUST` and `NotEnoughFunds` are validated before the wrapping arithmetic runs; there is no bound that a payment must be ≤ `MAX_MONEY` (21M BTC) or that the sum fits in `u64`. Whether the result is a panic (debug/overflow-checks) or a silent wrap (release without checks) depends on build profile, but both are reachable and harmful.

### Recommendation
Use `checked_add`/`checked_mul`/`checked_sum`-style arithmetic for `input_sat`, `payment_sat`, `needed_fee`, `fee_with_change`, and the `payment_sat + needed_fee` comparison, returning a new `TransactionError::Overflow` (or `NotEnoughFunds`) on failure. Additionally, reject individual payment amounts exceeding Bitcoin's `MAX_MONEY` bound before summing, since no valid output can exceed it.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// With overflow-checks disabled (release), this bypasses NotEnoughFunds:
let huge = u64::MAX / 2 + 1;
let payments = vec![
  (p2tr_script_buf(key).unwrap(), huge),
  (p2tr_script_buf(key).unwrap(), huge), // payment_sat wraps to ~1
];
// input_sat (e.g. 100_000 sats) < wrapped payment_sat + needed_fee is false,
// so the check at send.rs:215 passes and a SignableTransaction is returned
// whose outputs total ~u64::MAX sats against ~100_000 sats of inputs.
let tx = SignableTransaction::new(inputs, &payments, None, None, FEE).unwrap();
// tx.fee() (send.rs:139) then underflows as well; the signed transaction is
// consensus-invalid and can never confirm.
```
Relevant code: `payment_sat` sum at [1](#0-0) , unchecked fee multiplication at [2](#0-1) , the bypassed solvency check at [3](#0-2) , and the unchecked input sum at [4](#0-3) .

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L175-175)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-206)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```
