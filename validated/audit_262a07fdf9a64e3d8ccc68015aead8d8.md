### Title
Asymmetric rounding between "current disabled-collateral value" and "value being removed" can spuriously revert full collateral withdrawal - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
In the disabled-collateral branch of `collateral-remove`, the same token amount is normalized to USD twice with **opposite rounding directions**: once (rounded down) to compute the position's current disabled-collateral value, and once (rounded up) to compute the value being withdrawn. When a user withdraws their entire disabled-collateral balance, the rounded-up removal value can exceed the rounded-down current value by one unit, causing the `>=` assertion to fail and the withdrawal to revert even though the user is fully entitled to it. This is the same underlying bug class as the reported `totalBorrowedCredit` issue: two quantities that are supposed to represent the same underlying amount are computed through diverging scaling paths, and a later subtraction/comparison between them can fail non-deterministically, breaking otherwise-legitimate operations.

### Finding Description
`collateral-remove` handles the case where the asset being removed is not enabled as protocol-wide collateral (`is-collateral-enabled` is false) by computing its notional value on the fly from the price feed, rather than from the cached `notional-valued-assets` sum: [1](#0-0) 

```
(disabled-notional (normalize (* user-amount price) decimals false))
(removal-notional (normalize (* amount price) decimals true))
(total-collateral-value (+ collateral-value disabled-notional))
(asserts! (>= total-collateral-value removal-notional) ERR-INSUFFICIENT-COLLATERAL)
(is-healthy (- total-collateral-value removal-notional) debt-value current-ltvb)
```

The identical mainnet-deployed logic appears here: [2](#0-1) 

`normalize` is called with a boolean rounding flag (`false` for the "current balance" value, `true` for the "amount being removed" value). When the user removes their **entire** disabled-collateral balance (`amount == user-amount`), both calls multiply the exact same `price * amount` product, but because one is rounded down (`disabled-notional`) and the other rounded up (`removal-notional`), `removal-notional` can be `1` unit larger than `disabled-notional` at certain price/decimals combinations. Since `collateral-value` (the sum of *enabled* collateral) can legitimately be `u0` for a user whose only collateral is this disabled asset, `total-collateral-value = collateral-value + disabled-notional` can end up strictly less than `removal-notional`, tripping `ERR-INSUFFICIENT-COLLATERAL` and reverting the withdrawal for a fully-collateralized, fully-repaid position.

This mirrors the ECG `totalBorrowedCredit` root cause: two values meant to represent the *same* quantity (the token amount converted to USD) are derived via two different rounding paths, and a downstream `>=`/subtraction check between them can fail even when the "real" balances are perfectly consistent — a state the caller cannot control or predict, since it depends purely on the price and decimal values at call time.

### Impact Explanation
This falls under **temporary freezing of funds**: a user with a fully disabled collateral position (and no or fully-repaid debt tracked through `debt-value`) can be blocked from withdrawing 100% of their own collateral purely due to rounding, until the oracle price shifts enough to close the 1-unit gap. Unlike the ECG bug (which affected a system-wide `debtCeiling` read), this is scoped to the disabled-collateral removal path per-user, but the mechanism — an assertion comparing two independently-rounded USD notional values of the same underlying amount — is structurally identical.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires (a) an asset that is *disabled* as protocol-wide collateral but still individually held/removable by a user, (b) the user removing their entire balance of that asset, and (c) a price/decimals combination where the two roundings diverge by exactly one unit and `collateral-value` from other (enabled) collateral is insufficient to absorb the 1-unit gap. This is most likely to manifest for low-decimal or unusually-priced assets, or for positions where the disabled asset is the sole collateral.

### Recommendation
Use the **same** rounding direction for both the "current value" and "value being removed" computations of the same amount (both round down, matching the conservative direction used elsewhere for collateral valuation), or better, derive `removal-notional` as a pro-rata fraction of `disabled-notional` (`removal-notional = disabled-notional * amount / user-amount`) so that removing the full balance always yields `removal-notional == disabled-notional` by construction, eliminating the divergence entirely.

### Proof of Concept
Not independently executable from static review (would require a Clarinet/Stacks devnet with a disabled-collateral asset configured, a Pyth/DIA feed value, and decimals chosen such that `(* user-amount price)` is not evenly divisible by `10^decimals`). Conceptually:
1. Configure an asset as non-collateral-enabled (`is-collateral-enabled = false`) with `decimals` and a price such that `user-amount * price` is not a multiple of `10^decimals`.
2. User deposits `user-amount` of this asset as their only collateral, with zero debt tracked in `collateral-value`/`debt-value` (or fully repaid debt).
3. User calls `collateral-remove` with `amount == user-amount` (full withdrawal).
4. `disabled-notional = normalize(user-amount * price, decimals, false)` rounds down; `removal-notional = normalize(amount * price, decimals, true)` rounds up on the identical product — `removal-notional > disabled-notional` by 1.
5. `total-collateral-value (= 0 + disabled-notional) < removal-notional` ⇒ `(asserts! (>= total-collateral-value removal-notional) ...)` fails with `ERR-INSUFFICIENT-COLLATERAL`, reverting a legitimate full withdrawal.

Note: I was unable to retrieve the exact body of the `normalize` function in this session (only its 4 call-site matches were located) to confirm the precise semantics of the `true`/`false` rounding argument; this analysis assumes the conventional round-up/round-down interpretation implied by its usage pattern (`false` for balance valuation, `true` for amount-to-be-removed valuation). This should be verified directly against `normalize`'s implementation in `market.clar` before treating this as confirmed.

### Citations

**File:** local-testing/contracts/market/market.clar (L1168-1177)
```text
                (let ((oracle-data (get oracle asset))
                      (price (unwrap! (price-resolve oracle-data) ERR-DISABLED-COLLATERAL-PRICE-FAILED))
                      (decimals (get decimals asset))
                      (user-amount (find-collateral-amount (get collateral pos-full) asset-id))
                      (disabled-notional (normalize (* user-amount price) decimals false))
                      (removal-notional (normalize (* amount price) decimals true))
                      (total-collateral-value (+ collateral-value disabled-notional)))
                  (asserts! (>= total-collateral-value removal-notional) ERR-INSUFFICIENT-COLLATERAL)
                  (is-healthy (- total-collateral-value removal-notional) debt-value current-ltvb)))
            ERR-UNHEALTHY)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1145-1153)
```text
                (let ((oracle-data (get oracle asset))
                      (price (unwrap! (price-resolve oracle-data) ERR-DISABLED-COLLATERAL-PRICE-FAILED))
                      (decimals (get decimals asset))
                      (user-amount (find-collateral-amount (get collateral pos-full) asset-id))
                      (disabled-notional (normalize (* user-amount price) decimals false))
                      (removal-notional (normalize (* amount price) decimals true))
                      (total-collateral-value (+ collateral-value disabled-notional)))
                  (asserts! (>= total-collateral-value removal-notional) ERR-INSUFFICIENT-COLLATERAL)
                  (is-healthy (- total-collateral-value removal-notional) debt-value current-ltvb)))
```
