### Title
Integer overflow in payment/fee summation lets unbounded attacker-influenced amounts bypass the NotEnoughFunds check and produce an unspendable or panic-inducing transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The iputils CVE is an integer-overflow-on-crafted-input bug: a zero timestamp produces a large intermediate which overflows when squared during statistics, corrupting results / crashing the process. The analog in Serai's in-scope code is the unchecked `u64` arithmetic in `SignableTransaction::new` (and `fee()`), where attacker-influenced satoshi amounts are summed with plain `+`/`-`/`*`. Overflow of `payment_sat` wraps it to a small value, bypassing the `NotEnoughFunds` check at `send.rs:215`, after which a transaction is constructed whose outputs exceed the total Bitcoin supply — a transaction that will never confirm (permanent loss of the inputs' fee and a signing round spent on a malformed message), or a panic in `fee()` at `send.rs:139-140` when outputs exceed inputs.

### Finding Description
`SignableTransaction::new` sums input and payment values using plain `u64` arithmetic with no `checked_add`/`checked_sum`:

- `send.rs:175`: `let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();`
- `send.rs:187`: `let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();`
- `send.rs:206`: `let mut needed_fee = fee_per_vbyte * vbytes;`
- `send.rs:215`: `if input_sat < (payment_sat + needed_fee) { Err(NotEnoughFunds) }`
- `send.rs:139-140`: `fee()` computes `sum(inputs) - sum(outputs)` with a plain subtraction.

The only validation on each payment amount is the dust lower bound (`send.rs:165-169`). There is no upper bound and no overflow checking. If `payments` contains amounts whose sum exceeds `u64::MAX` (e.g., three payments of `u64::MAX / 2`), `payment_sat` wraps to a small value. The check at line 215 then compares `input_sat` against `wrapped_payment_sat + needed_fee`, which passes even though the real total of the `TxOut`s pushed at `send.rs:188-191` vastly exceeds the inputs. `Amount::from_sat` accepts any `u64`, so outputs with values above Bitcoin's 21M cap are built without complaint (no `MoneyRange` check is ever applied).

Similarly, `input_sat` itself can overflow: `ReceivedOutput` values come from `ReceivedOutput::read` (`wallet/mod.rs:122-134`), which consensus-decodes an attacker-supplied `TxOut` — the 8-byte value field is fully attacker-controlled up to `u64::MAX`. A wrapping `input_sat` produces a wrong `fee()` result and a wrong `change` value via `input_sat.checked_sub(payment_sat + fee_with_change)` at `send.rs:228`, which can emit a change output larger than the real inputs.

Like the CVE, this is an incomplete-input-validation → integer-overflow bug: edge-case (near-`u64::MAX`) values were not considered, so arithmetic on them overflows into wrong results or a panic.

### Impact Explanation
When the sums overflow:

- A `SignableTransaction` is returned `Ok` and handed to the FROST `Schnorr` algorithm (`crypto.rs:94-160`) to be signed. The resulting transaction is invalid under Bitcoin consensus rules (output amounts fail `MoneyRange`, or `fee()` panics on underflow at `send.rs:139-140`), so every input committed to it is burned for a signing session that can never settle — a denial of service of the signing pipeline and, in a processor flow, leaked fees / stuck plans.
- The change calculation at `send.rs:228-233` can mint a change output far larger than `input_sat`, reporting value as received/retained that is not actually spendable.
- In debug builds (and with `overflow-checks` enabled in the workspace `Cargo.toml`), the wraps panic outright — a reachable crash from arithmetic on values influenced by external bytes.

### Likelihood Explanation
Reaching the overflow requires payment amounts or `ReceivedOutput` values summing past `u64::MAX`. Payment amounts are requester-supplied, and `ReceivedOutput::read` accepts a fully attacker-controlled `TxOut` whose `value` field is arbitrary 8 bytes; nothing in `read` bounds it. Honest integrator configurations won't hit this, which is why it fits Medium rather than High — but the code performs zero defense (no `checked_*`, no `Amount::from_sat` range validation, no `MAX_MONEY` bound), so any path that admits large values corrupts the solvency invariant the `NotEnoughFunds` check is supposed to enforce.

### Recommendation
- Accumulate `input_sat` and `payment_sat` with `checked_add`/`try_fold`, and compute `payment_sat + needed_fee` with `checked_add`, returning `NotEnoughFunds`/`TransactionError` on overflow.
- Reject `TxOut`/`ReceivedOutput` values above `bitcoin::Amount::MAX_MONEY` (21M BTC) at `ReceivedOutput::read` and at `SignableTransaction::new`.
- Compute `fee()` with `checked_sub` and treat underflow as an error rather than panicking.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// payments whose sum wraps u64::MAX; each individual amount passes the dust check
let huge = u64::MAX / 2;
let payments = vec![(addr(), huge), (addr(), huge), (addr(), huge)];
// payment_sat wraps to (3 * huge) mod 2^64 = huge - 2, still "payable" by a small input
// input_sat < payment_sat + needed_fee is FALSE -> NotEnoughFunds is bypassed
// tx_outs get Amount::from_sat(huge) each -> outputs >> total supply, and
// fee() at send.rs:139 underflows/panics since sum(outputs) > sum(inputs)
let tx = SignableTransaction::new(inputs, &payments, Some(change_addr()), None, FEE);
// Ok(...) is returned for a transaction Bitcoin can never accept
```