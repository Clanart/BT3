### Title
`stSTXbtc`/`zstSTXbtc` priced using raw STX/USD feed with no yield-ratio transformation, unlike its sibling `stSTX` - (File: `local-testing/contracts/utility/protocol-data.clar` / `mainnet/contracts/utility/v0-1-data.clar`, function `get-asset-price`)

### Summary
`get-asset-price` prices `stSTXbtc` (and its ztoken `zstSTXbtc`) by directly reading the `PYTH-STX` feed with no conversion, even though the asset is documented as "Liquid staked STX (BTC yield)" that accrues value distinct from raw STX — unlike `stSTX`, which correctly applies a staking ratio (`get-ststx-ratio`) on top of the STX price before being used as collateral value.

### Finding Description
In `get-asset-price`, `stSTX` collateral value is computed as `stx-price * ratio / STSTX-RATIO-DECIMALS`, explicitly correcting the raw STX price for the staking exchange rate before it's used for collateral valuation: [1](#0-0) 

For `stSTXbtc`, however, the price is taken as-is from the STX feed with no analogous ratio/transformation, even though `docs/vaults.md` states this vault's underlying is "Liquid staked STX (BTC yield)" — i.e. a token whose real value diverges from 1:1 STX over time as BTC-denominated rewards accrue: [2](#0-1) 

This mirrors the C4 root cause pattern: a value meant to represent a distinct, yield-bearing unit (Stargate's staked LP amount vs. WETH) is substituted for the base reference unit (raw STX price) without applying the necessary conversion, producing a value in the wrong unit that then flows straight into the solvency/health check via `sum-collateral-usd` → `is-healthy`: [3](#0-2) [4](#0-3) 

The same pattern is duplicated in `market.clar`'s `resolve-callcode`, where `CALLCODE-ZSTSTX` explicitly chains `resolve-ststx` before `resolve-ztoken`, but `CALLCODE-ZSTSTXBTC` skips any equivalent ratio step and passes the raw price straight into `resolve-ztoken`: [5](#0-4) 

### Impact Explanation
If `stSTXbtc`'s true USD value legitimately diverges from the raw STX price (as the documentation implies via "BTC-denominated yield"), the protocol's on-chain valuation of `stSTXbtc`/`zstSTXbtc` collateral used in `is-healthy` checks will be systematically wrong in one fixed direction (understated, since no yield uplift is ever applied). This can cause solvent positions collateralized by `stSTXbtc`/`zstSTXbtc` to be misclassified as unhealthy, triggering premature/incorrect liquidations — a temporary freezing of user funds, with liquidators profiting by seizing undervalued collateral. Conversely, if the omission instead means the yield is meant to accrue purely through the vault's liquidity index (not the spot price) and `stSTXbtc` genuinely trades 1:1 with STX by design, this would not be a bug but a deliberate design choice; I could not conclusively verify from the available index which of these is true.

### Likelihood Explanation
The asymmetry is directly visible by comparing the `stSTX` and `stSTXbtc` code paths side by side — one applies a ratio transformation, the sibling does not — despite the documentation describing `stSTXbtc` as a distinct BTC-yield-bearing asset. This makes the analog structurally strong, though confidence is limited by the inability to locate a dedicated `get-ststxbtc-ratio`-style oracle/contract call in the indexed code that would confirm whether such a ratio is genuinely required or intentionally absent.

### Recommendation
Confirm with the protocol team whether `stSTXbtc`'s USD value should track a staking/yield ratio analogous to `stSTX`. If so, implement and apply an equivalent ratio-fetch (e.g., `get-ststxbtc-ratio`) in `get-asset-price` and in `market.clar`'s `resolve-callcode` (`CALLCODE-ZSTSTXBTC`) before applying the liquidity-index ztoken transformation, mirroring the `CALLCODE-ZSTSTX` pattern.

### Proof of Concept
1. `stSTXbtc` accrues BTC-denominated yield over time such that 1 `stSTXbtc` becomes redeemable for more value than 1 raw STX.
2. `get-asset-price(stSTXbtc)` still returns exactly `PYTH-STX` price with no uplift. [6](#0-5) 
3. A borrower posts `stSTXbtc`/`zstSTXbtc` as collateral; `sum-collateral-usd` undervalues the true worth of the position using this stale price.
4. `is-healthy` incorrectly flags the position as under-collateralized, permitting a liquidator to trigger liquidation and seize collateral at a discount relative to its real market value, freezing the borrower's excess value. [4](#0-3)

### Citations

**File:** local-testing/contracts/utility/protocol-data.clar (L517-523)
```text
(define-private (sum-collateral-usd (entry { aid: uint, amount: uint }) (acc uint))
  (let ((aid (get aid entry))
        (amount (get amount entry))
        (asset-data (unwrap-panic (contract-call? .assets get-status aid)))
        (decimals (get decimals asset-data))
        (price (get-asset-price aid)))
    (+ acc (/ (* amount price) (pow u10 decimals)))))
```

**File:** local-testing/contracts/utility/protocol-data.clar (L552-556)
```text
  ;; stSTX - STX price x stSTX ratio
  (if (is-eq aid stSTX) 
      (let ((stx-price (default-to u0 (get-pyth-price PYTH-STX)))
            (ratio (unwrap-panic (get-ststx-ratio))))
        (mul-div-down stx-price ratio STSTX-RATIO-DECIMALS))
```

**File:** local-testing/contracts/utility/protocol-data.clar (L588-594)
```text
  ;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
  (if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
  ;; zstSTXbtc - stSTXbtc price x liquidity index
  (if (is-eq aid zstSTXbtc)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index stSTXbtc)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
```

**File:** local-testing/contracts/market/market.clar (L371-380)
```text
(define-private (resolve-callcode (p uint) (callcode (optional (buff 1))))
  (let ((cc (unwrap! callcode (ok p))))
    (if (is-eq cc CALLCODE-STSTX) (resolve-ststx p)
    (if (is-eq cc CALLCODE-ZSTX) (resolve-ztoken p STX)
    (if (is-eq cc CALLCODE-ZSBTC) (resolve-ztoken p sBTC)
    (if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken (try! (resolve-ststx p)) stSTX)
    (if (is-eq cc CALLCODE-ZUSDC) (resolve-ztoken p USDC)
    (if (is-eq cc CALLCODE-ZUSDH) (resolve-ztoken p USDH)
    (if (is-eq cc CALLCODE-ZSTSTXBTC) (resolve-ztoken p stSTXbtc)
    ERR-ORACLE-CALLCODE)))))))))
```

**File:** local-testing/contracts/market/market.clar (L678-681)
```text
(define-private (is-healthy (collateral-usd uint) (debt-usd uint) (ltv uint))
  (if (is-eq debt-usd u0)
      true
      (<= (* debt-usd BPS) (* collateral-usd ltv))))
```
