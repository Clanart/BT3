### Title
stSTXbtc / zstSTXbtc Collateral and Debt Valued Using STX Price Instead of BTC Price - ([File: mainnet/contracts/utility/v0-1-data.clar])

### Summary
In the `get-asset-price` helper, the branches for asset IDs `stSTXbtc` and `zstSTXbtc` are documented as returning "BTC price" but actually query the `PYTH-STX` feed instead of `PYTH-BTC`, attaching the wrong underlying asset's price to these positions.

### Finding Description
`get-asset-price` resolves a USD price per asset ID by dispatching to the correct Pyth/DIA feed per asset. For every other BTC-denominated asset (`sBTC`, `zsBTC`) the code correctly calls `(get-pyth-price PYTH-BTC)`. However for `stSTXbtc` and `zstSTXbtc` the comments explicitly state "BTC price" / "stSTXbtc price x liquidity index" but the calls use `PYTH-STX`: [1](#0-0) 

```
;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
(if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
;; zstSTXbtc - stSTXbtc price x liquidity index
(if (is-eq aid zstSTXbtc)
    (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
          (lindex (get-vault-liquidity-index stSTXbtc)))
      (mul-div-down btc-price lindex INDEX-PRECISION))
```

This is used by `sum-collateral-usd` and `sum-debt-usd`, the private folds that this data contract exposes for computing a position's aggregate USD collateral/debt value: [2](#0-1) [3](#0-2) 

Since STX and BTC prices diverge and move independently, valuing a BTC-yield asset (`stSTXbtc`/`zstSTXbtc`) at the STX price produces an arbitrarily wrong USD figure — the direction and magnitude of error tracks the STX/BTC price ratio at any given moment, not a fixed bias. Whoever benefits depends on whether STX is priced above or below BTC at the time: if STX/BTC > 1 the position is overvalued (benefits the holder — inflated collateral or understated debt); if STX/BTC < 1 it is undervalued (harms the holder — collateral looks smaller than it is, debt looks smaller than it is).

I could not fully confirm from the available index whether this "data" contract's output is consumed by any on-chain enforcement path (e.g., an external liquidator/keeper bot querying `sum-collateral-usd`/`sum-debt-usd` to decide when to call the market's liquidation entrypoint) or is purely an off-chain/frontend display helper. The core enforcement logic in `mainnet/contracts/market/v0-4-market.clar` (`price-resolve`, `resolve-callcode`, `get-asset-value`) uses the asset's registered `oracle` tuple (`type`/`ident`/`callcode`) rather than this hardcoded `PYTH-STX`/`PYTH-BTC` dispatch, so the actual on-chain health check and liquidation trigger do not appear to go through this buggy function.

### Impact Explanation
If this contract's price/health output is relied upon by liquidators, integrators, or automated risk tooling to decide when a `stSTXbtc`/`zstSTXbtc` position should be liquidated, the wrong-asset price causes a wrong health verdict: positions that are actually undercollateralized can be reported healthy (delaying liquidation — temporary freezing of recoverable bad-debt value) or healthy positions can be reported unhealthy (unwarranted liquidation causing loss to the borrower). Given the uncertainty about whether this data path gates an actual state-changing operation versus being purely informational, I cannot confirm this reaches the Critical/High bar with certainty from the code alone.

### Likelihood Explanation
No privileged access or governance action is required to trigger the bug — it fires on every read of `get-asset-price`/`sum-collateral-usd`/`sum-debt-usd` for `stSTXbtc`/`zstSTXbtc` positions whenever STX and BTC prices diverge, which happens continuously in normal market conditions.

### Recommendation
Change the `stSTXbtc` and `zstSTXbtc` branches in `get-asset-price` to call `(get-pyth-price PYTH-BTC)` instead of `(get-pyth-price PYTH-STX)`, matching the existing comments and the pattern used for `sBTC`/`zsBTC`.

### Proof of Concept
1. Observe STX/USD and BTC/USD diverge (e.g., STX price rises relative to BTC).
2. Call any read-only function in `v0-1-data.clar` that surfaces `get-asset-price(stSTXbtc)` or aggregates via `sum-collateral-usd`/`sum-debt-usd` for a position holding `stSTXbtc`/`zstSTXbtc`.
3. Compare the reported USD value against the true value computed with the BTC feed — the reported figure will be off by the STX/BTC price ratio, i.e., the wrong asset's price was attached to the position. [4](#0-3)

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L509-516)
```text
;; Helper: Sum collateral USD values
(define-private (sum-collateral-usd (entry { aid: uint, amount: uint }) (acc uint))
  (let ((aid (get aid entry))
        (amount (get amount entry))
        (asset-data (unwrap-panic (contract-call? .v0-assets get-status aid)))
        (decimals (get decimals asset-data))
        (price (get-asset-price aid)))
    (+ acc (/ (* amount price) (pow u10 decimals)))))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L526-535)
```text
;; Helper: Sum debt USD values
(define-private (sum-debt-usd (entry { aid: uint, scaled: uint }) (acc uint))
  (let ((aid (get aid entry))
        (scaled (get scaled entry))
        (asset-data (unwrap-panic (contract-call? .v0-assets get-status aid)))
        (decimals (get decimals asset-data))
        (borrow-index (get-vault-borrow-index aid))
        (actual (mul-div-down scaled borrow-index INDEX-PRECISION))
        (price (get-asset-price aid)))
    (+ acc (/ (* actual price) (pow u10 decimals)))))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L540-589)
```text
(define-private (get-asset-price (aid uint))
  ;; STX - Pyth oracle
  (if (is-eq aid STX) (default-to u0 (get-pyth-price PYTH-STX))
  ;; sBTC - Pyth oracle (BTC price)
  (if (is-eq aid sBTC) (default-to u0 (get-pyth-price PYTH-BTC))
  ;; stSTX - STX price x stSTX ratio
  (if (is-eq aid stSTX) 
      (let ((stx-price (default-to u0 (get-pyth-price PYTH-STX)))
            (ratio (unwrap-panic (get-ststx-ratio))))
        (mul-div-down stx-price ratio STSTX-RATIO-DECIMALS))
  ;; USDC - Pyth oracle
  (if (is-eq aid USDC) (default-to u0 (get-pyth-price PYTH-USDC))
  ;; USDH - DIA oracle
  (if (is-eq aid USDH) (default-to u0 (get-dia-price DIA-USDH))
  ;; zSTX - STX price x liquidity index
  (if (is-eq aid zSTX)
      (let ((stx-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index STX)))
        (mul-div-down stx-price lindex INDEX-PRECISION))
  ;; zsBTC - BTC price x liquidity index
  (if (is-eq aid zsBTC)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-BTC)))
            (lindex (get-vault-liquidity-index sBTC)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
  ;; zstSTX - stSTX price x liquidity index (stSTX already includes ratio)
  (if (is-eq aid zstSTX)
      (let ((stx-price (default-to u0 (get-pyth-price PYTH-STX)))
            (ratio (unwrap-panic (get-ststx-ratio)))
            (ststx-price (mul-div-down stx-price ratio STSTX-RATIO-DECIMALS))
            (lindex (get-vault-liquidity-index stSTX)))
        (mul-div-down ststx-price lindex INDEX-PRECISION))
  ;; zUSDC - USDC price x liquidity index
  (if (is-eq aid zUSDC)
      (let ((usdc-price (default-to u0 (get-pyth-price PYTH-USDC)))
            (lindex (get-vault-liquidity-index USDC)))
        (mul-div-down usdc-price lindex INDEX-PRECISION))
  ;; zUSDH - USDH price x liquidity index
  (if (is-eq aid zUSDH)
      (let ((usdh-price (default-to u0 (get-dia-price DIA-USDH)))
            (lindex (get-vault-liquidity-index USDH)))
        (mul-div-down usdh-price lindex INDEX-PRECISION))
  ;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
  (if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
  ;; zstSTXbtc - stSTXbtc price x liquidity index
  (if (is-eq aid zstSTXbtc)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index stSTXbtc)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
  ;; Unknown asset - return 0
  u0)))))))))))))
```
