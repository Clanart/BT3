### Title
Integer overflow in payment/fee arithmetic lets `SignableTransaction::new` produce and sign a consensus-invalid transaction whose outputs exceed its inputs - (networks/bitcoin/src/wallet/send.rs)

### Summary
The Perl sprintf bug class is *attacker-chosen size/width values overflowing the computed buffer length, so the produced output is silently corrupted*. The Serai analog lives in `SignableTransaction::new`, which computes the total payment amount and required funds with unchecked `u64` `sum`/`+` arithmetic on caller-supplied payment amounts. The sum can wrap, bypassing the `NotEnoughFunds` check, and the change calculation then emits a `TxOut` worth more than the inputs — yielding a transaction the FROST multisig signs but Bitcoin will never accept.

### Finding Description
`SignableTransaction::new` performs three unchecked additions over attacker-influenced `u64` values:

- `payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>()` at line 187 — `sum` on `u64` wraps in release builds.
- `input_sat < (payment_sat + needed_fee)` at line 215 — a plain `+` which can overflow.
- `input_sat.checked_sub(payment_sat + fee_with_change)` at line 228 — the inner `+` is again unchecked, so `checked_sub` only protects the subtraction, not the wrapped sum. [1](#0-0) [2](#0-1) [3](#0-2) 

If `payment_sat + needed_fee` wraps to a small value, `NotEnoughFunds` is bypassed. The change branch then computes `value = input_sat - wrapped_sum`, which is `>= DUST` for almost any real input set, so a change output worth essentially the entire input value is pushed alongside payment outputs whose declared values already exceed the total money supply. `Amount::from_sat` performs no 21M-BTC bound check, so the invalid outputs are accepted into `tx_outs`. `SignableTransaction::multisig`/`TransactionSignMachine::sign` verify only that each prevout's `script_pubkey` matches the offset group key (lines 275-281) and sign the taproot sighash of this malformed transaction — the signing path never re-checks output-vs-input conservation.

### Impact Explanation
The result is a threshold-signed Bitcoin transaction that is consensus-invalid (`outputs > inputs`, and individual output values exceeding `MAX_MONEY`), so it can never be broadcast or confirmed, yet the Serai plan/eventuality machinery treats the inputs as consumed by this signing attempt. Funds are rendered unspendable: the multisig committed its signatures to a transaction that cannot exist on-chain, and the plan cannot complete. This is the direct analog of "the format string's computed size overflowed and the produced output was corrupt" — here the computed value/change amount overflows and the produced *transaction* is corrupt.

### Likelihood Explanation
Payment `(ScriptBuf, u64)` pairs flow from user-initiated inbound instructions (deposits carry attacker-chosen refund/payment intent), so the payment amounts are reachable by an unprivileged external party. Triggering requires payment amounts near `u64::MAX`, which exceeds any real deposit — however the same unchecked pattern (`sum::<u64>()` on `input_sat`/`payment_sat`, `fee_per_vbyte * vbytes`) can also underflow the `needed_fee < min_relay` comparison or wrap `payment_sat` itself when aggregated across many payments, and the check ordering means any wrap of `payment_sat + fee` defeats solvency enforcement. Reachability of the extreme amounts depends on the processor's plan construction not independently bounding amounts, but the arithmetic flaw is unconditional and unprivileged inputs reach the function.

### Recommendation
Use `checked_add`/`checked_sum` for `input_sat`, `payment_sat`, `payment_sat + needed_fee`, `payment_sat + fee_with_change`, and `fee_per_vbyte * vbytes`, returning `TransactionError::NotEnoughFunds`/`Overflow` on failure. Additionally, reject `payment.1 > MAX_MONEY` (21M * 10^8 sats) alongside the existing `DUSTPayment` check so a single output can never exceed the supply cap.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs — SignableTransaction::new
// Given one real input `input_sat` (e.g. 100_000 sats) and attacker-chosen payments:
let payments = [
  (attacker_script_a, u64::MAX),          // passes `*amount < DUST` check
  (attacker_script_b, u64::MAX),          // payment_sat wraps: u64::MAX*2 -> u64::MAX - 1
  // choose amounts so payment_sat + needed_fee wraps to a small value v
];
// Line 215: input_sat < (payment_sat + needed_fee)  ->  100_000 < v  -> false, no error
// Line 228: input_sat.checked_sub(payment_sat + fee_with_change) -> Some(~100_000)
//   -> a change output worth ~all inputs is added next to two u64::MAX outputs.
// TransactionSignMachine::sign then FROST-signs sighashes for a transaction whose
// outputs total ~2*2^64 sats >> inputs — consensus-invalid, funds stuck.
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
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

**File:** networks/bitcoin/src/wallet/send.rs (L227-234)
```rust
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```
