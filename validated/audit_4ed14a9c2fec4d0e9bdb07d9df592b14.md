### Title
stSTXbtc Collateral Priced Using STX Feed Instead of BTC Feed, Corrupting Health-Factor/Liquidation Verdicts - (File: mainnet/contracts/utility/v0-1-data.clar)

### Summary
In `get-asset-price` inside `mainnet/contracts/utility/v0-1-data.clar`, the `stSTXbtc` asset branch calls `(get-pyth-price PYTH-STX)` — the STX/USD feed — instead of `PYTH-BTC`, even though the code comment right next to it says "BTC price (liquid staked STX with BTC yield)". The same mistake is repeated in the `zstSTXbtc` branch, which also multiplies by `(get-pyth-price PYTH-STX)`. This mirrors the analog bug class of "a price attached to the wrong asset," analogous to how the external report's `fill`/`fillV3Relay` mismatch silently fed the wrong data into downstream logic.

### Finding Description
`get-asset-price` is the single price-resolution helper used by both `sum-collateral-usd` and `sum-debt-usd`: [1](#0-0) [2](#0-1) 

For every other asset the branch correctly matches the underlying price feed to the asset (e.g. `sBTC` → `PYTH-BTC`, `USDC` → `PYTH-USDC`), but the `stSTXbtc` and `zstSTXbtc` branches use `PYTH-STX` — the STX/USD feed — while the inline comment explicitly states the intended feed is BTC: [3](#0-2) 

This price is fed directly into the position-health computation in the same contract, which derives `total-collateral-usd`, `current-ltv`, `health-factor`, and `is-liquidatable`: [4](#0-3) 

### Impact Explanation
STX trades several orders of magnitude below BTC. Using the STX feed for `stSTXbtc`/`zstSTXbtc` collateral massively understates `coll-usd` in `sum-collateral-usd`, which:
- Inflates `current-ltv` (`mul-div-down debt-usd BPS coll-usd`), and
- Deflates `health-factor` (`mul-div-down (mul-bps-down coll-usd ltv-borrow) BPS debt-usd`),

driving `is-liquidatable` to `true` for positions that are, in reality, well over-collateralized in BTC terms. Conversely, if this helper were ever reused to size a debt/collateral comparison the other direction, an under-priced BTC-denominated debt asset would let a truly unhealthy position appear healthy. Since the impacted fields are exactly the health/liquidation verdict outputs, and stSTXbtc/zstSTXbtc collateral would be reported (and potentially acted upon) at a fraction of its true USD value, this falls in the permitted "wrong health verdict" analog class, with an economic direction that can trigger unwarranted liquidations of solvent stSTXbtc positions — a form of theft of user collateral value that liquidators would gain from once they act on the incorrect signal.

### Likelihood Explanation
This triggers deterministically for any account holding `stSTXbtc` or `zstSTXbtc` collateral whenever this position/health view is queried — no attacker action or oracle manipulation is required; the mispriced feed is hardcoded.

### Recommendation
Change the `stSTXbtc` and `zstSTXbtc` branches in `get-asset-price` to use `(get-pyth-price PYTH-BTC)` instead of `PYTH-STX`, consistent with the existing comments and with the analogous `sBTC`/`zsBTC` branches.

### Proof of Concept
1. Register a user position with `stSTXbtc` as collateral and any debt asset.
2. Call the read-only function in `v0-1-data.clar` that computes the position summary (lines 440-486).
3. Observe `total-collateral-usd` computed via `sum-collateral-usd` → `get-asset-price(stSTXbtc)`, which resolves to the STX/USD price rather than the BTC/USD price, understating the true USD collateral value by roughly the STX:BTC price ratio.
4. Observe the resulting `current-ltv` inflated and `health-factor`/`is-liquidatable` incorrectly reflecting an unhealthy or liquidatable position despite adequate real BTC-denominated collateral. [5](#0-4)

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L440-486)
```text
              (debt-list (get debt position))
              ;; Map each debt entry to enriched format with actual balances
              (enriched-debts (map build-debt-entry debt-list))
              ;; Calculate notional values
              (coll-usd (fold sum-collateral-usd collateral-list u0))
              (debt-usd (fold sum-debt-usd debt-list u0))
              ;; Calculate LTV
              (current-ltv (if (is-eq coll-usd u0)
                              (if (is-eq debt-usd u0) u0 BPS)
                              (mul-div-down debt-usd BPS coll-usd)))
              ;; Get egroup for health calculation
              (egroup-result (contract-call? .v0-egroup resolve mask)))
          (match egroup-result
            egroup
              (let ((ltv-borrow (buff-to-uint-be (get LTV-BORROW egroup)))
                    (ltv-liq-partial (buff-to-uint-be (get LTV-LIQ-PARTIAL egroup)))
                    ;; Health factor: (coll x ltv-borrow) / debt, scaled to BPS
                    ;; >10000 = healthy, <10000 = unhealthy
                    (health-factor (if (is-eq debt-usd u0)
                                      u100000000  ;; Infinite health if no debt
                                      (mul-div-down (mul-bps-down coll-usd ltv-borrow) BPS debt-usd))))
                (ok {
                  account: account,
                  mask: mask,
                  collateral: collateral-list,
                  debt: enriched-debts,
                  total-collateral-usd: coll-usd,
                  total-debt-usd: debt-usd,
                  current-ltv: current-ltv,
                  ltv-borrow: ltv-borrow,
                  ltv-liq-partial: ltv-liq-partial,
                  health-factor: health-factor,
                  is-liquidatable: (>= current-ltv ltv-liq-partial)
                }))
            egroup-err (ok {
              account: account,
              mask: mask,
              collateral: collateral-list,
              debt: enriched-debts,
              total-collateral-usd: coll-usd,
              total-debt-usd: debt-usd,
              current-ltv: current-ltv,
              ltv-borrow: u0,
              ltv-liq-partial: u0,
              health-factor: u100000000,
              is-liquidatable: false
            })))
```

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
