## Analog Found: Rounding of `debt-actual` to Zero in `process-debt-asset` Allows Collateral Seizure Without Debt Repayment

### Title
Liquidation debt-to-token conversion can round `debt-actual` to zero while `debt-actual-usd` stays positive, letting a liquidator seize collateral for free - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The Trail of Bits `AddLiquidity` finding shows that when a single input amount feeds two independent `amount * X / Y` divisions, one division can floor to zero while the other stays positive, letting a party receive value without paying the corresponding side. `v0-4-market.clar`'s liquidation pipeline has the same two-division structure: `process-debt-asset` computes `debt-actual-usd` (rounded down) from a liquidator-supplied `debt-amount`, then re-derives the debt token amount `debt-actual` from that USD figure by dividing by `debt-price`. For a high-priced, low-decimal debt asset (e.g. sBTC, 8 decimals, ~$60k price), a liquidator can pick a `debt-amount` small enough that `debt-actual-usd` is a tiny but non-zero integer while `debt-actual = debt-actual-usd * 10^decimals / debt-price` truncates to `0`.

### Finding Description
`process-debt-asset` at [1](#0-0)  computes:
```
debt-usd = normalize(debt-amount * debt-price, debt-decimals, round-up=false)
debt-actual-usd = min(debt-usd, max-debt-usd)
debt-actual = mul-div-down(debt-actual-usd, 10^debt-decimals, debt-price)
```
Both divisions round down. `normalize` truncates toward zero [2](#0-1) , matching the same `ediv...car` truncation pattern the report calls out in Dexter's `AddLiquidity`. Because `debt-usd` is derived from `debt-amount`, and `debt-actual` is derived back from `debt-usd`, this is a round trip through two independent floor divisions — exactly the shape of the reported bug (`tokensDeposited = amount*tokenPool/xtzPool` vs `lqtMinted = amount*lqtTotal/xtzPool`).

Downstream, `process-collateral-asset` computes `coll-usd-expected = calc-liq-collateral-repay(debt-actual-usd, liq-penalty)` and `coll-expected`/`coll-actual` from that non-zero `debt-actual-usd` [3](#0-2) , and `scale-debt-for-liquidation` converts `debt-final` (derived from `debt-final-usd`, itself seeded from `debt-actual-usd`) into `scaled-to-remove` and `debt-to-repay` for on-chain debt/collateral movement [4](#0-3) . Because `debt-actual-usd` is non-zero even when `debt-actual` (the token amount) rounds to zero, the collateral-side calculations proceed on a non-zero USD figure while the actual debt token transfer amount can be zero.

I was not able to fully trace the final `liquidate` public function body (its `debt-actual`/`debt-final`/`coll-final` wiring into the actual `ft-transfer`/vault calls) within the available tool budget — the grep confirmed 104 references to these identifiers in the file but I could not read the complete function to verify whether a final `> debt-to-repay u0` or `> coll-final u0` guard exists before executing the transfers. This is the key unresolved point: if such a floor guard exists on `debt-to-repay` (analogous to the `repay` function's `(asserts! (> repaid-scaled-debt u0) ERR-INSUFFICIENT-SCALED-DEBT)` at [5](#0-4) ), this specific path is not exploitable and the report's short-term mitigation ("prevent tokensDeposited from being zero") is effectively already applied elsewhere in this codebase (e.g. `repay`).

### Impact Explanation
If the zero-floor is *not* guarded before the actual token transfer/debt-removal in `liquidate`, a liquidator could seize a non-zero amount of collateral (`coll-actual`/`coll-final`) while repaying `debt-to-repay = 0` debt tokens — direct theft of user funds at rest (the borrower's collateral), landing in the Critical impact category.

### Likelihood Explanation
Exploitability strictly requires (a) confirming no floor guard exists downstream of `process-debt-asset`/`scale-debt-for-liquidation` before the transfer, and (b) a debt asset with high price/low decimals combined with a liquidator choosing a minimal `debt-amount`. Given the uncertainty in point (a), likelihood cannot be confirmed from the available context.

### Recommendation
Add an explicit `(asserts! (> debt-to-repay u0) ERR-...)` (and/or `(> debt-actual u0)`) check immediately after `process-debt-asset`/`scale-debt-for-liquidation` and before any collateral transfer or debt-removal call in `liquidate`, mirroring the existing zero-guard in `repay` at [5](#0-4) . Long term, apply fuzzing/property tests asserting `debt-actual > 0 ⟺ coll-final > 0` (or bound minimum liquidatable USD amounts) across the liquidation math helpers (`process-debt-asset`, `process-collateral-asset`, `calc-final-liquidation-amounts`, `scale-debt-for-liquidation`).

### Proof of Concept
Conceptual (not verified end-to-end due to incomplete trace of `liquidate`):
1. Borrower has an unhealthy position with sBTC debt (8 decimals, price ≈ 6,000,000,000,000 in 8-decimal precision) and some other collateral.
2. Liquidator calls `liquidate` with `debt-amount` chosen so that `debt-usd = debt-amount * debt-price / 10^8` truncates to a value `v` such that `debt-actual = v * 10^8 / debt-price` truncates to `0` (i.e., `v < debt-price / 10^8`, which is trivially satisfiable for `v` in the low single digits given sBTC's large price).
3. If no zero-check exists on `debt-to-repay`/`debt-actual` before the vault debt-removal/collateral transfer, `process-collateral-asset` and `scale-debt-for-liquidation` still compute a non-zero `coll-actual`/`coll-final` from the non-zero intermediate `debt-actual-usd`, and the liquidator receives collateral while contributing zero debt tokens.

Given I could not confirm the presence/absence of a terminal zero-guard, this should be verified directly in the codebase (full body of `liquidate` in `mainnet/contracts/market/v0-4-market.clar`) before treating this as a confirmed vulnerability rather than a potential analog.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L576-580)
```text
(define-private (normalize (value uint) (decimals uint) (round-up bool))
  (let ((decimal-factor (pow u10 decimals)))
    (if round-up
      (div-up value decimal-factor)
      (div-down value decimal-factor))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L761-783)
```text
(define-private (process-debt-asset
  (debt-amount uint)
  (debt-aid uint)
  (max-debt-usd uint)
  (assets (list 64 {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool, price: uint
  })))
  (let ((debt-asset-info (unwrap-panic (find-asset debt-aid assets)))
        (debt-price (get price debt-asset-info))
        (debt-decimals (get decimals debt-asset-info))
        (debt-usd (normalize (* debt-amount debt-price) debt-decimals false))
        ;; cap debt at maximum liquidatable amount
        (debt-actual-usd (if (> debt-usd max-debt-usd) max-debt-usd debt-usd))
        ;; convert capped USD amount back to token amount
        (debt-actual (mul-div-down debt-actual-usd (pow u10 debt-decimals) debt-price)))
    {
      debt-actual-usd: debt-actual-usd,
      debt-actual: debt-actual,
      debt-price: debt-price,
      debt-decimals: debt-decimals
    }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L789-829)
```text
(define-private (process-collateral-asset
  (coll-aid uint)
  (debt-actual-usd uint)
  (liq-penalty uint)
  (user-coll-balance uint)
  (assets (list 64 {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool, price: uint
  }))
  (coll-asset {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool
  }))
  
  (let (;; Calculate expected collateral in USD (with penalty bonus for liquidator)
        (coll-usd-expected (calc-liq-collateral-repay debt-actual-usd liq-penalty))
        
        ;; Handle disabled collaterals by resolving price if not in enabled assets
        (coll-asset-info (match (find-asset coll-aid assets)
                           ;; Found in enabled list: use it (already has price)
                           found found
                           ;; Not found (disabled): resolve price on demand
                           (let ((oracle-data (get oracle coll-asset))
                                 (price (unwrap-panic (price-resolve oracle-data))))
                             (merge coll-asset { price: price }))))
        (coll-price (get price coll-asset-info))
        (coll-decimals (get decimals coll-asset-info))
        (coll-expected (mul-div-down coll-usd-expected (pow u10 coll-decimals) coll-price))
        
        ;; cap at available collateral (user may not have enough)
        (coll-actual (if (> coll-expected user-coll-balance)
                         user-coll-balance
                         coll-expected)))
    {
      coll-actual: coll-actual,
      coll-expected: coll-expected,
      coll-price: coll-price,
      coll-decimals: coll-decimals
    }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L858-877)
```text
(define-private (scale-debt-for-liquidation
  (debt-final uint)
  (coll-actual uint)
  (curr-scaled uint)
  (asset-id uint))
  (let (;; convert debt amount to scaled units for storage
        (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id))))
        (scaled-debt (mul-div-down debt-final INDEX-PRECISION borrow-index))
        ;; cap at current debt (prevent over-repayment)
        (scaled-to-remove (if (> scaled-debt curr-scaled) curr-scaled scaled-debt))
        (debt-to-repay (mul-div-up scaled-to-remove borrow-index INDEX-PRECISION))
        ;; If debt was capped, scale collateral proportionally
        (coll-final (if (< scaled-to-remove scaled-debt)
                        (mul-div-down coll-actual scaled-to-remove scaled-debt)
                        coll-actual)))
    {
      scaled-to-remove: scaled-to-remove,
      debt-to-repay: debt-to-repay,
      coll-final: coll-final
    }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1349-1349)
```text

```
