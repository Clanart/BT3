### Title
Unchecked u64 additions in `SignableTransaction::new` allow output totals to exceed inputs, producing an unspendable signed transaction - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The Allora M-9 bug class is "a cap/limit exists, but the mutation is committed before checking it, so a downstream accounting computation goes negative and breaks a periodic system path." In `serai`'s Bitcoin wallet, the "cap" is `input_sat` (the funds actually available). `SignableTransaction::new` is supposed to guarantee `inputs >= payments + fee` before a `SignableTransaction` is constructed and later signed, but the comparisons and sums are performed with plain `u64` arithmetic that can wrap, defeating the check.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

1. `payment_sat` is computed with `payments.iter().map(|payment| payment.1).sum::<u64>()` (line 187). `needed_fee = fee_per_vbyte * vbytes` (line 206) is an unchecked `u64` multiply. The affordability check then does `input_sat < (payment_sat + needed_fee)` (line 215) with an unchecked `u64` add. If `payment_sat` is close to `u64::MAX`, `payment_sat + needed_fee` wraps to a small value and the `NotEnoughFunds` guard is bypassed — the analog of Allora minting `tokensToMint` before checking `ecosystemMintSupplyRemaining`.

2. The change path uses `input_sat.checked_sub(payment_sat + fee_with_change)` (line 228), which *also* sums in `u64` first; a wrapped `payment_sat + fee_with_change` yields a large bogus `value`, so a change output is created worth more than the inputs — a consensus-invalid transaction.

3. Once constructed, the corruption mirrors the "negative remaining supply" consequence: `SignableTransaction::fee()` (lines 138–141) computes `sum(inputs) - sum(outputs)`; with outputs > inputs this underflows and panics, and the transaction passed to `SignableTransaction::sign` can never confirm on Bitcoin, while Serai-side code treats the payments as executed.

The dust check at lines 165–169 only bounds each payment from below; nothing bounds the aggregate `payment_sat` or `payment_sat + fee` before the arithmetic is committed, exactly the missing pre-check in the Allora report.

### Impact Explanation
An attacker who can cause large-valued payments (burn/withdrawal instructions carry attacker-chosen `u64` amounts) can craft payment sets whose sums overflow. The resulting `SignableTransaction` is either a consensus-invalid Bitcoin transaction (outputs exceeding inputs) that the multisig will still sign — so burned funds are reported as paid but are never spendable — or it triggers a `u64` underflow panic in `fee()`/change computation inside transaction handling. Both outcomes match the report's shape: the cap check is bypassed and downstream accounting breaks.

### Likelihood Explanation
Reachable through public inputs: payment amounts are attacker-influenced `u64` values carried in instructions, and multiple payments are summed without overflow checks. Triggering requires amounts near `u64::MAX`, so it needs either a very large legitimate denomination or multiple crafted payments summing to wrap; not every transaction is exploitable, but no validator collusion or privileged access is required — consistent with a Medium-severity analog.

### Recommendation
Use `checked_add`/`checked_mul`/`saturating` arithmetic for `payment_sat`, `needed_fee = fee_per_vbyte * vbytes`, and the `payment_sat + needed_fee` / `payment_sat + fee_with_change` sums, returning `TransactionError::NotEnoughFunds`/`TooLargeTransaction` on overflow — i.e., clamp before the state is committed, per the Allora fix pattern (`tokensToMint = min(tokensToMint, ecosystemMintSupplyRemaining)`):

```rust
let payment_sat = payments.iter().try_fold(0u64, |a, p| a.checked_add(p.1))
  .ok_or(TransactionError::NotEnoughFunds { inputs: input_sat, payments: u64::MAX, fee: needed_fee })?;
let fee_total = payment_sat.checked_add(needed_fee).ok_or(TransactionError::NotEnoughFunds { .. })?;
if input_sat < fee_total { ... }
```

### Proof of Concept
Construct `SignableTransaction::new` with one small real input (e.g., 1000 sats) and payments `[(addr, u64::MAX - 10), (addr, 20)]` (each ≥ `DUST`):

- `payment_sat = u64::MAX + 9` wraps to `9` (release) or panics; choose values so `payment_sat + needed_fee` wraps below `input_sat`.
- The `input_sat < payment_sat + needed_fee` check passes.
- `input_sat.checked_sub(payment_sat + fee_with_change)` succeeds with a wrapped sum, producing a change output of ~`u64::MAX` sats.
- Result: a `SignableTransaction` whose outputs vastly exceed inputs — invalid on the Bitcoin network — and `tx.fee()` underflows/panics when invoked.