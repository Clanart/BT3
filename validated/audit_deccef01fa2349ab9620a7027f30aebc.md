### Title
Integer overflow in Bitcoin transaction construction allows fee check bypass and crash when signing crafted payments - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`SignableTransaction::new` sums attacker-influenced payment amounts into `payment_sat` and computes `payment_sat + needed_fee` with unchecked u64 arithmetic. Overflow of either the sum or the addition wraps the value, bypassing the `NotEnoughFunds` check and causing an underflow panic in `fee()` — a remote denial of service via crafted input amounts, analogously to the integer-overflow-to-crash class of CVE-2017-5499.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`), the total payment amount and fee check are computed with plain `u64` arithmetic: [1](#0-0) [2](#0-1) 

`payment_sat` is `payments.iter().map(|p| p.1).sum::<u64>()` and `needed_fee` is `fee_per_vbyte * vbytes`; the solvency test is `input_sat < (payment_sat + needed_fee)`. None of the sum, the multiplication, or the addition use checked arithmetic, so an input whose `payment_sat + needed_fee` exceeds `u64::MAX` wraps to a small value and passes the `NotEnoughFunds` check even though `input_sat < payment_sat`.

After the check, the change path uses `checked_sub` (`send.rs:228`), so no change output is pushed, and a `SignableTransaction` is returned whose outputs exceed its inputs. Any later call to `fee()` (`send.rs:138-141`) computes `sum(prevouts) - sum(outputs)` with unchecked subtraction, which panics on underflow (debug builds) or wraps to a nonsense fee (release builds). Separately, `payment_sat` itself can overflow during the `sum::<u64>()` when many payments are specified.

### Impact Explanation
- Denial of service: a signing/scheduler process calling `SignableTransaction::new` or `fee()` on a transaction crafted with such amounts panics, matching the crash impact of the reference CVE.
- Consistency violation: the `NotEnoughFunds` guard is bypassed, so a `SignableTransaction` representing a transaction that can never be valid on-chain (outputs > inputs) is produced and handed to the FROST signing machines. The multisig can end up attesting/signing an unintended, unspendable transaction.

### Likelihood Explanation
Reachable when `payments` or `fee_per_vbyte` are derived from untrusted request data (e.g., withdrawal/payment plans fed into the scheduler) rather than hard-coded values. An attacker needs a payment vector whose `u64` sum (plus `needed_fee`, or `fee_per_vbyte * vbytes` product) overflows. Since `Amount` values are user-supplied `u64`s and only a `DUST` lower-bound check is applied per payment (`send.rs:165-169`), there is no upper bound preventing this. Exploitation requires no threshold collusion or key compromise — only the ability to submit payment amounts.

### Recommendation
Use `checked_add`/`checked_mul`/`checked_sum` (or `u128` intermediate arithmetic) for `payment_sat`, `needed_fee = fee_per_vbyte * vbytes`, and the `payment_sat + needed_fee` comparison, returning `TransactionError::NotEnoughFunds`/`TooLowFee` on overflow. Apply the same checked arithmetic in `fee()`. Additionally bound each payment amount by `MAX_MONEY` (21e14 sats) to reject nonsensical outputs early.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// Construct a SignableTransaction where payment_sat + needed_fee overflows u64.
let payments = vec![(script.clone(), u64::MAX - 100)]; // passes DUST check
// needed_fee = fee_per_vbyte * vbytes is small (e.g. ~100 sats)
// payment_sat + needed_fee wraps to ~0, so:
//   input_sat < (payment_sat + needed_fee)  ->  input_sat < ~0  -> false
// check passes even though input_sat (e.g. 1000 sats) << payment_sat.
let stx = SignableTransaction::new(inputs, &payments, None, None, 1).unwrap();
// Outputs exceed inputs; fee() underflows:
stx.fee(); // panics: attempt to subtract with overflow (or wraps in release)
```
With multiple payments, `payment_sat` itself can wrap inside `sum::<u64>()` (e.g. two payments of `u64::MAX / 2 + 1`), likewise defeating the solvency check.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-221)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```
