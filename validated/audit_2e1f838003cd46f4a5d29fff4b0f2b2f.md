### Title
stSTXbtc / zstSTXbtc valued using STX price feed instead of BTC price feed, corrupting collateral/debt USD and liquidation verdicts - (File: `mainnet/contracts/utility/v0-1-data.clar`)

### Summary
`get-asset-price` in `v0-1-data.clar`, the function that prices every asset ID for collateral/debt valuation and health-factor computation, prices `stSTXbtc` and `zstSTXbtc` using the `PYTH-STX` feed instead of the `PYTH-BTC` feed, despite the code explicitly labeling the value `btc-price` and commenting that it should be the BTC price.

### Finding Description
`get-asset-price` resolves a per-asset USD price used by `get-user-position` to compute `total-collateral-usd`, `total-debt-usd`, `current-ltv`, `health-factor`, and `is-liquidatable`: [1](#0-0) 

For asset ID `stSTXbtc` (a BTC-tracking, liquid-staked-STX-yielding token) the price is fetched from the STX feed, not the BTC feed: [2](#0-1) 

The same wrong feed is reused for `zstSTXbtc`, where the local binding is even named `btc-price` while it is populated from `get-pyth-price PYTH-STX`: [3](#0-2) 

Compare this with the correct pattern used for `sBTC`/`zsBTC`, which correctly reference `PYTH-BTC`: [4](#0-3) [5](#0-4) 

This is the "price attached to the wrong asset" bug class explicitly listed as in-scope: the code fetches a real, valid oracle price, but binds it to the wrong asset identifier before using it in USD math.

### Impact Explanation
`stSTXbtc`/`zstSTXbtc` collateral or debt USD values computed in `sum-collateral-usd`, `sum-debt-usd`, and `build-debt-entry` (via `get-asset-price`) are keyed to the STX/USD price rather than BTC/USD, which differ by orders of magnitude. This directly corrupts `total-collateral-usd`, `total-debt-usd`, `current-ltv`, `health-factor`, and `is-liquidatable` returned by `get-user-position`: [6](#0-5) [7](#0-6) 

Since BTC/USD >> STX/USD, `stSTXbtc`/`zstSTXbtc` collateral is massively undervalued and debt in this asset is massively undervalued too. Undervalued collateral understates a user's actual borrowing power and health (potential wrongful liquidation eligibility / temporary freezing of funds for the affected user); undervalued debt understates a borrower's real liability, letting the same account appear healthier than it is and permitting it to withdraw/borrow more elsewhere than solvency allows (protocol insolvency risk / theft-adjacent mispricing benefiting the borrower at the protocol's expense). Both directions land squarely on a wrong health verdict as required by the rules.

### Likelihood Explanation
This triggers deterministically any time `get-user-position` (or any function reading through `get-asset-price` for asset IDs `stSTXbtc`/`zstSTXbtc`) is called for an account holding this asset as collateral or debt — no attacker action, oracle manipulation, or race condition is required; it is a straightforward coding/config bug reachable via a standard read path.

### Recommendation
Change the two occurrences to use `PYTH-BTC` instead of `PYTH-STX`:
```clarity
;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
(if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-BTC))
...
;; zstSTXbtc - stSTXbtc price x liquidity index
(if (is-eq aid zstSTXbtc)
    (let ((btc-price (default-to u0 (get-pyth-price PYTH-BTC)))
          (lindex (get-vault-liquidity-index stSTXbtc)))
      (mul-div-down btc-price lindex INDEX-PRECISION))
```
Add a regression test asserting `get-asset-price stSTXbtc` tracks the BTC feed and diverges from the STX feed value.

### Proof of Concept
1. Configure/mock the Pyth storage contract so `PYTH-STX` and `PYTH-BTC` return distinct prices (e.g., STX = $1, BTC = $60,000), matching real-world divergence.
2. Call `(contract-call? .v0-1-data get-asset-price stSTXbtc)` — observe it returns the $1-scale STX price instead of the $60,000-scale BTC price.
3. Call `(contract-call? .v0-1-data get-user-position account)` for an account holding `stSTXbtc`/`zstSTXbtc` as collateral or debt — observe `total-collateral-usd`/`total-debt-usd`, `current-ltv`, `health-factor`, and `is-liquidatable` are computed off the wrong (STX) price, diverging from the true BTC-denominated value by orders of magnitude.

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L434-487)
```text
(define-read-only (get-user-position (account principal))
  (let ((enabled-mask (contract-call? .v0-assets get-bitmap)))
    (match (contract-call? .v0-market-vault get-position account enabled-mask)
      position
        (let ((mask (get mask position))
              (collateral-list (get collateral position))
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
      err-code ERR-NO-POSITION)))
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

**File:** mainnet/contracts/utility/v0-1-data.clar (L543-544)
```text
  ;; sBTC - Pyth oracle (BTC price)
  (if (is-eq aid sBTC) (default-to u0 (get-pyth-price PYTH-BTC))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L559-563)
```text
  ;; zsBTC - BTC price x liquidity index
  (if (is-eq aid zsBTC)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-BTC)))
            (lindex (get-vault-liquidity-index sBTC)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L581-582)
```text
  ;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
  (if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L583-587)
```text
  ;; zstSTXbtc - stSTXbtc price x liquidity index
  (if (is-eq aid zstSTXbtc)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index stSTXbtc)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
```
