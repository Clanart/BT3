### Title
Integer overflow in Bitcoin transaction fee/funds arithmetic bypasses `NotEnoughFunds` and yields an unspendable signed transaction - ([File: networks/bitcoin/src/wallet/send.rs](https://github.com))

### Summary
The CVE-2023-38652 class — unchecked integer overflow on attacker-influenced count/size arithmetic — maps onto `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`. Several `u64` additions and multiplications on caller-controlled payment amounts and fee rates are performed without overflow checks, letting crafted inputs wrap the balance checks and produce a consensus-invalid transaction that Serai still signs.

### Finding Description
`SignableTransaction::new` sums the requested payments and computes fees with plain `u64` arithmetic:

- `payment_sat` is a `sum::<u64>()` over caller-provided payment amounts.
- `needed_fee = fee_per_vbyte * vbytes` can overflow `u64` (line 206).
- The solvency check `if input_sat < (payment_sat + needed_fee)` uses unchecked addition (line 215). If `payment_sat + needed_fee` wraps to a small value, the `NotEnoughFunds` rejection is skipped even though the outputs exceed the inputs.
- The change path uses `input_sat.checked_sub(payment_sat + fee_with_change)` (line 228), but the inner `payment_sat + fee_with_change` itself is still an unchecked, wrappable addition.

If the wrap-around succeeds, the resulting `Transaction` has `sum(outputs) > sum(inputs)`, i.e. a negative fee, which is consensus-invalid. `SignableTransaction::fee()` then computes `sum(inputs) - sum(outputs)` with an underflowing subtraction (lines 139–141), which panics in debug builds and wraps in release. Either way, the multisig machine proceeds to `multisig()`/`sign()`, so the threshold group signs a transaction that can never be broadcast.

Reachability: `payments` and `fee_per_vbyte` are caller-supplied plan parameters ultimately derived from user withdrawal requests; an unprivileged party can specify a payment amount near `u64::MAX` (well above Bitcoin's 21M BTC cap, which is never validated here — only the `DUST` lower bound is checked at lines 165–169).

### Impact Explanation
- Bypass of the `NotEnoughFunds` solvency check via wrapping arithmetic.
- The threshold signature machine signs a transaction that is invalid under Bitcoin consensus (outputs exceed inputs), so a plan can reach `SignCompleted` with a TX that can never confirm — funds are locked and the plan must be retried/aborted.
- `fee()` underflow panics in debug builds; in release it reports a bogus wrapped fee, which can confuse accounting of `needed_fee` versus actual fee.
- The unvalidated `fee_per_vbyte * vbytes` overflow can also silently produce a near-zero `needed_fee`, defeating the intended fee-rate policy independently of the solvency bug.

### Likelihood Explanation
Payment amounts flow from untrusted withdrawal/payment requests into `SignableTransaction::new` with only a dust lower-bound check and no upper bound (Bitcoin's `MAX_MONEY` is not enforced). Triggering the overflow requires only choosing `amount` such that `payment_sat + needed_fee > u64::MAX`, e.g. a single payment of `u64::MAX - small`. No validator collusion or privileged access is needed — just the ability to submit a payment request. Severity is bounded because the resulting TX is invalid rather than a theft of funds; the realistic outcome is a signed-but-unbroadcastable transaction and fee/accounting corruption, consistent with Medium.

### Recommendation
- Use `checked_add`/`checked_mul`/`checked_sub` (or `saturating_*` with explicit error) for `payment_sat`, `needed_fee`, `fee_with_change`, and the solvency comparison at lines 206–228.
- Reject payment amounts above `bitcoin::Amount::MAX_MONEY` / `MAX_STANDARD_TX_WEIGHT`-relevant bounds at construction time, not just `DUST`.
- Make `fee()` use `checked_sub` and return an error/`Option` instead of underflowing.

### Proof of Concept
```rust
// Conceptual: payments = [(script, u64::MAX - 1000)], inputs worth ~1 BTC
// payment_sat = u64::MAX - 1000
// needed_fee small => payment_sat + needed_fee wraps to a small value
// => `input_sat < wrapped` is false => NotEnoughFunds skipped
// change: checked_sub(payment_sat + fee_with_change) also wraps/fails => no change output
// Result: tx with output ~= u64::MAX sats, input = 1 BTC => negative fee, consensus-invalid,
// yet multisig() will sign it; fee() underflows when queried.
```