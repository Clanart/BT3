### Title
`stSTXbtc`/`zstSTXbtc` are priced with the STX/USD feed instead of BTC/USD, producing a wrong health/liquidation verdict for the vault-ststxbtc collateral - ([File: mainnet/contracts/utility/v0-1-data.clar])

### Summary
The read-only data/health utility contract prices the `stSTXbtc` collateral and its ztoken `zstSTXbtc` using the `PYTH-STX` feed even though the asset is documented and named as a BTC-yield-bearing liquid-staking token, whose value should track `PYTH-BTC`.

### Finding Description
`get-asset-price` resolves each asset's USD price. For `stSTXbtc` and `zstSTXbtc` it explicitly calls `get-pyth-price PYTH-STX`, with a comment mislabeling the result as `btc-price`: [1](#0-0) 

```
;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
(if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
;; zstSTXbtc - stSTXbtc price x liquidity index
(if (is-eq aid zstSTXbtc)
    (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
          (lindex (get-vault-liquidity-index stSTXbtc)))
      (mul-div-down btc-price lindex INDEX-PRECISION))
```

The vault's own documentation identifies `vault-ststxbtc` as "Underlying: Liquid staked STX (BTC yield)" / "Purpose: Liquid staking with BTC-denominated yield" [2](#0-1) , and the `PYTH-BTC` feed constant exists and is used elsewhere for `sBTC` pricing [3](#0-2) . This is exactly the "price attached to the wrong asset" analog class: the code fetches the STX/USD price where a BTC/USD price is intended for this asset, so `stSTXbtc`/`zstSTXbtc` valuations move 1:1 with STX price instead of BTC price.

This mispriced value feeds directly into the health/liquidation computation implemented in the same contract: `sum-collateral-usd` and `sum-debt-usd` both call `get-asset-price` and aggregate into `total-collateral-usd`/`total-debt-usd`, which are then used to compute `current-ltv`, `health-factor`, and the `is-liquidatable` verdict: [4](#0-3) [5](#0-4) 

### Impact Explanation
Because STX and BTC prices move independently and can diverge significantly in either direction, `is-liquidatable`/`health-factor` computed for any account holding `stSTXbtc`/`zstSTXbtc` as collateral or debt will be wrong whenever STX/USD and BTC/USD diverge:
- If BTC rallies relative to STX, the true USD value of `stSTXbtc` collateral is understated, so a position that should be liquidatable is reported healthy — this can allow the account to keep/extend unsafe debt (freezing/undercollateralization risk feeding into protocol insolvency once real collateral values are used elsewhere), or block deserved liquidations, letting bad debt accumulate.
- If BTC drops relative to STX, collateral value is overstated, letting accounts borrow more than their real BTC-denominated collateral supports, or evade liquidation — a path toward theft of protocol funds / undercollateralized positions and insolvency risk.

This lands in the in-scope **Critical** impact category (protocol insolvency / permanent freezing of funds) since the wrong health verdict systematically misprices one of the six collateral/debt assets in a core position/liquidation read path.

### Likelihood Explanation
No external compromise is required — this triggers purely from natural divergence between STX and BTC market prices, which happens continuously. Any account with `stSTXbtc`/`zstSTXbtc` collateral or debt is affected every time this data path is queried for health assessment.

### Recommendation
Change the `stSTXbtc` and `zstSTXbtc` branches in `get-asset-price` to call `get-pyth-price PYTH-BTC` instead of `PYTH-STX`, matching the documented BTC-denominated yield of the underlying `ststxbtc-token-v2` asset. Add a regression test asserting `stSTXbtc`/`zstSTXbtc` valuations track `PYTH-BTC` and diverge correctly from STX price movements. Audit all other `get-asset-price` branches (and the analogous logic if duplicated elsewhere) for the same feed-to-asset mismatch.

### Proof of Concept
1. Suppose STX/USD = $1.00 and BTC/USD = $100,000 (arbitrary large divergence).
2. A user deposits `stSTXbtc` as collateral; the true USD value of their holding, being BTC-denominated, should be priced off `PYTH-BTC`.
3. `get-asset-price stSTXbtc` instead calls `get-pyth-price PYTH-STX`, returning ~$1.00 (or whatever STX trades at) instead of the BTC-denominated value.
4. `sum-collateral-usd` uses this wrong price to compute `total-collateral-usd`, which flows into `current-ltv`/`health-factor`/`is-liquidatable` in the same function.
5. Depending on the direction of STX vs BTC price divergence, the reported health status is either falsely healthy (blocking a deserved liquidation) or falsely unhealthy/over-valued (allowing over-borrowing) — either way the health verdict for the account is wrong and diverges from the true BTC-denominated collateral value.

*Note: I could not fully confirm within the available context whether `mainnet/contracts/utility/v0-1-data.clar`'s `get-asset-price`/health-factor path is also consumed as the authoritative source for on-chain liquidation execution (as opposed to being a read-only convenience/UI contract) or whether the actual liquidation-triggering logic in `v0-4-market.clar` uses a separate, correctly-configured oracle resolution path (`price-resolve`, which reads oracle config from the asset registry rather than this hardcoded mapping). If `v0-4-market.clar`'s liquidation path does not call into this utility contract, the impact would be limited to any consumer that trusts this contract's health-factor/is-liquidatable output (e.g., off-chain liquidator bots, front-ends, or other contracts) rather than the core on-chain liquidation state itself — a background agent or deeper trace should verify all callers of `v0-1-data.clar`'s health-check entrypoint before finalizing severity.*

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

**File:** mainnet/contracts/utility/v0-1-data.clar (L444-473)
```text
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
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L509-535)
```text
;; Helper: Sum collateral USD values
(define-private (sum-collateral-usd (entry { aid: uint, amount: uint }) (acc uint))
  (let ((aid (get aid entry))
        (amount (get amount entry))
        (asset-data (unwrap-panic (contract-call? .v0-assets get-status aid)))
        (decimals (get decimals asset-data))
        (price (get-asset-price aid)))
    (+ acc (/ (* amount price) (pow u10 decimals)))))

;; Helper: Find specific asset amount in collateral list
(define-private (find-collateral-amount-iter
  (entry { aid: uint, amount: uint })
  (acc { target: uint, amount: uint }))
  (if (is-eq (get aid entry) (get target acc))
      { target: (get target acc), amount: (get amount entry) }
      acc))

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

**File:** mainnet/contracts/utility/v0-1-data.clar (L581-587)
```text
  ;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
  (if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
  ;; zstSTXbtc - stSTXbtc price x liquidity index
  (if (is-eq aid zstSTXbtc)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index stSTXbtc)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
```

**File:** docs/vaults.md (L232-236)
```markdown
### 6. vault-ststxbtc
- **Underlying:** Liquid staked STX (BTC yield)
- **Ztoken:** zstSTXbtc
- **Purpose:** Liquid staking with BTC-denominated yield

```
