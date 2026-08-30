### Title
stSTXbtc/zstSTXbtc priced using the STX/USD feed instead of BTC/USD, grossly mispricing the asset in USD-value calculations - ([File: mainnet/contracts/utility/v0-1-data.clar])

### Summary
`get-asset-price` in `mainnet/contracts/utility/v0-1-data.clar` resolves the USD price of `stSTXbtc` and `zstSTXbtc` by calling `get-pyth-price PYTH-STX` — the STX/USD feed — while the surrounding comments explicitly say "BTC price" is intended. `PYTH-BTC` is defined and correctly used for `sBTC`/`zsBTC`, but is never referenced for the `stSTXbtc` branches, so the wrong price feed (and therefore the wrong order of magnitude) is attached to this asset.

### Finding Description
`get-asset-price` computes the canonical 8-decimal USD price used to value every asset id in the protocol: [1](#0-0) 

For `sBTC`/`zsBTC` the function correctly calls the BTC feed: [2](#0-1) 

But for `stSTXbtc` and `zstSTXbtc`, despite the inline comment stating "BTC price", the code calls `get-pyth-price PYTH-STX` (the STX feed) instead of `PYTH-BTC`: [3](#0-2) 

This is the exact "price attached to the wrong asset" bug class: the function fetches a real, valid, fresh oracle price — just for the wrong underlying — so no stale/confidence/staleness guard in the oracle layer would ever catch it, since from the oracle's perspective the STX/USD read is perfectly legitimate.

This price is consumed directly by the USD aggregation helpers used to build account health data: [4](#0-3) [5](#0-4) 

These feed `total-collateral-usd`, `total-debt-usd`, `current-ltv`, `health-factor`, and `is-liquidatable` in the account-position result structure (as seen in the surrounding code returning these fields), i.e. the exact values that determine whether a position is healthy, borrowable, or liquidatable.

### Impact Explanation
STX trades several orders of magnitude below BTC. Because `stSTXbtc`/`zstSTXbtc` is priced as if it were STX (~$1 range) instead of BTC (~$100k range), any USD valuation of this asset is wrong by roughly that same multiple:
- If `stSTXbtc`/`zstSTXbtc` is deposited as **collateral**, `total-collateral-usd` is understated by the same factor, causing legitimate positions to appear under-collateralized or unable to support borrowing proportional to their real value — a freezing of the true value of that collateral.
- If `stSTXbtc`/`zstSTXbtc` is **borrowed** (the corresponding vault, `v0-vault-ststxbtc.clar`, supports borrowing this asset), `total-debt-usd` is understated by the same factor. A borrower's computed `current-ltv`/`health-factor` would appear far healthier than the real economic exposure, letting a position be treated as safe (not liquidatable, and potentially eligible for further borrowing) while it is actually borrowing real BTC-denominated value nearly "for free" in USD-accounting terms — this is the direction that benefits the attacker/borrower at the protocol's expense and can lead to insolvency if debt is realized against undercollateralized positions.

This lands on the Critical impact class (protocol insolvency / theft of funds) if the debt-side path is reachable for actual borrow/liquidation gating, or at minimum High (temporary/permanent freezing of collateral value) on the collateral side.

### Likelihood Explanation
The bug is deterministic and always active — it does not depend on oracle manipulation, staleness, or any registry misconfiguration; it is a hardcoded wrong constant (`PYTH-STX` instead of `PYTH-BTC`) inside the protocol's own pricing function, contradicted by its own inline comment. Any account holding `stSTXbtc`/`zstSTXbtc` as collateral or debt is affected on every valuation call.

Note on verification limits: I was not able to fully confirm within the available searches whether `market.clar`'s live borrow/liquidation-authorization path calls this exact `get-asset-price`/`sum-collateral-usd`/`sum-debt-usd` utility function versus resolving prices independently through its own DAO-configured asset registry (`price-resolve`/`resolve-callcode` in `v0-4-market.clar`). The structure and field names returned alongside this computation (`current-ltv`, `ltv-borrow`, `ltv-liq-partial`, `health-factor`, `is-liquidatable`) strongly suggest this utility function is the account-health/liquidation-eligibility source of truth, but confirming the exact call graph would require further inspection of `mainnet/contracts/market/v0-4-market.clar`'s account-health entrypoints and `mainnet/contracts/proposals/mainnet/v0-init.clar` wiring.

### Recommendation
Change the `stSTXbtc` and `zstSTXbtc` branches of `get-asset-price` to call `get-pyth-price PYTH-BTC` instead of `PYTH-BTC`'s STX counterpart, matching the existing correct pattern used for `sBTC`/`zsBTC`. Add a unit test asserting that `get-asset-price stSTXbtc` tracks the BTC feed and diverges from `get-asset-price STX`, to prevent regression of this specific feed-asset mapping.

### Proof of Concept
1. Observe `get-asset-price` for `stSTXbtc`/`zstSTXbtc` at [3](#0-2) : both branches call `(get-pyth-price PYTH-STX)`.
2. Given STX price ≈ $1 and BTC price ≈ $100,000+, any `stSTXbtc` amount priced through this path is valued at roughly 1/100,000th (or whatever the current STX/BTC ratio is) of its real USD value.
3. A user deposits/borrows `stSTXbtc`; `sum-collateral-usd`/`sum-debt-usd` (lines 510-516, 527-535) use this mispriced value to compute `total-collateral-usd`/`total-debt-usd`, and downstream `current-ltv`/`health-factor`/`is-liquidatable` are computed from these wrong totals — resulting in a wrong health verdict for any account holding this asset.

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
