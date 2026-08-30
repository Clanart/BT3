### Title
`get-asset-price()` prices `stSTXbtc`/`zstSTXbtc` off the STX/USD feed instead of BTC/USD, corrupting health/liquidation reads in the data-utility contract - (File: `mainnet/contracts/utility/v0-1-data.clar`)

### Summary
`get-asset-price` in `v0-1-data.clar` is meant to resolve the USD price for every registered asset, including `stSTXbtc` (id 10) and its vault token `zstSTXbtc` (id 11). Both branches are commented as "BTC price" but call `get-pyth-price PYTH-STX` (the STX/USD feed) rather than `PYTH-BTC`. This mislabels one asset's price with a completely unrelated feed, which is the same class of "price attached to wrong asset" defect flagged in the external report's counter-mismatch bug class, just manifesting in this codebase's oracle-resolution path rather than a counter.

### Finding Description
In `get-asset-price`:
```
mainnet/contracts/utility/v0-1-data.clar:581-587
;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
(if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
;; zstSTXbtc - stSTXbtc price x liquidity index
(if (is-eq aid zstSTXbtc)
    (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
          (lindex (get-vault-liquidity-index stSTXbtc)))
      (mul-div-down btc-price lindex INDEX-PRECISION))
```
`PYTH-STX` (`mainnet/contracts/utility/v0-1-data.clar:43`) is the STX/USD identifier, while `PYTH-BTC` (`mainnet/contracts/utility/v0-1-data.clar:45`) is the BTC/USD identifier that is correctly used for the plain `sBTC`/`zsBTC` branches a few lines above (`mainnet/contracts/utility/v0-1-data.clar:544,561`). `stSTXbtc` is a BTC-denominated liquid-staking token per its own comment, yet its price is computed from the STX feed. Every downstream consumer of `get-asset-price` — `sum-collateral-usd`, `sum-debt-usd`, and therefore `get-user-position`'s `health-factor`/`is-liquidatable` output (`mainnet/contracts/utility/v0-1-data.clar:447-493`) — will report USD values for `stSTXbtc`/`zstSTXbtc` positions using STX's price instead of BTC's, an error of roughly two orders of magnitude given typical STX vs BTC prices.

### Impact Explanation
`v0-1-data.clar` is a read-only "protocol-data" utility contract (`mainnet/contracts/utility/v0-1-data.clar:1-6`) that batches state for external consumption (frontends/monitoring/automation), and it does not itself execute transfers, borrows, or liquidations — those state-changing paths in `v0-4-market.clar` resolve prices independently via the assets registry's oracle configuration, not through this hardcoded mapping. I could not find any on-chain caller of `get-user-position`/`get-asset-price` that gates fund movement (`grep_search` for `v0-1-data` and `get-user-position` returned only the contract's own definitions and test-type bindings, no calls from `v0-4-market.clar` or other state-changing contracts). Without confirmation that automated liquidation bots, keeper contracts, or other privileged callers rely on this specific read-only function's `is-liquidatable`/`health-factor` output to trigger real liquidations or borrow/withdraw permissions, I cannot establish that this bug directly causes theft or freezing of funds on-chain — it primarily produces incorrect off-chain-consumed data.

### Likelihood Explanation
The bug is deterministic and triggers on every call to `get-asset-price` for `stSTXbtc`/`zstSTXbtc` (ids 10/11), which are live registered assets per the mainnet asset ID scheme. However, since it lives in a read-only aggregator contract whose outputs I could not trace into any state-changing function, likelihood of on-chain financial impact is unconfirmed.

### Recommendation
Change both branches to use `PYTH-BTC` instead of `PYTH-STX`:
```clarity
(if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-BTC))
(if (is-eq aid zstSTXbtc)
    (let ((btc-price (default-to u0 (get-pyth-price PYTH-BTC)))
          (lindex (get-vault-liquidity-index stSTXbtc)))
      (mul-div-down btc-price lindex INDEX-PRECISION))
```
Additionally, audit whether any external integrator, keeper, or off-chain liquidation bot consumes `get-user-position`'s `health-factor`/`is-liquidatable` fields to decide when to call the real `liquidate` function in `v0-4-market.clar`; if so, this becomes a direct trigger for missed or premature liquidations against `stSTXbtc` positions.

### Proof of Concept
Not applicable as a fund-theft PoC given the uncertainty above — this is a data-correctness defect. To observe it: call `get-asset-price(stSTXbtc)` or `get-asset-price(zstSTXbtc)` on `v0-1-data.clar` and compare the returned value against the actual BTC/USD price; it will match the STX/USD price instead, off by roughly the STX/BTC price ratio. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L41-47)
```text
;; -- Oracle: Pyth price feed IDs (mainnet)
;; STX/USD: https://pyth.network/price-feeds/crypto-stx-usd
(define-constant PYTH-STX 0xec7a775f46379b5e943c3526b1c8d54cd49749176b0b98e02dde68d1bd335c17)
;; BTC/USD: https://pyth.network/price-feeds/crypto-btc-usd
(define-constant PYTH-BTC 0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43)
;; USDC/USD: https://pyth.network/price-feeds/crypto-usdc-usd
(define-constant PYTH-USDC 0xeaa020c61cc479712813461ce153894a96a6c00b21ed0cfc2798d1f9a9e9c94a)
```

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
