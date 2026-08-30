### Title
Wrong Pyth Feed Used for stSTXbtc/zstSTXbtc Price Resolution Corrupts Health Factor and LTV - (File: `mainnet/contracts/utility/v0-1-data.clar`)

### Summary
In `get-asset-price` in `v0-1-data.clar`, the price for `stSTXbtc` and `zstSTXbtc` is computed using the `PYTH-STX` feed instead of `PYTH-BTC`, even though the accompanying comments explicitly state "BTC price". This is a mismatch of the "price attached to the wrong asset" class: the code fetches the wrong Pyth feed for an asset, causing every downstream USD notional, LTV, and health-factor computation for these assets to be wrong.

### Finding Description
`get-asset-price` resolves per-asset USD prices for use in collateral/debt valuation and account health computation: [1](#0-0) 

For `stSTXbtc` the comment reads "BTC price (liquid staked STX with BTC yield)" but the call is `(get-pyth-price PYTH-STX)`, not `(get-pyth-price PYTH-BTC)`. The same wrong feed is reused for `zstSTXbtc`'s liquidity-index-scaled variant. Compare with the correctly-implemented sibling assets in the same function, `sBTC` and `zsBTC`, which correctly call `PYTH-BTC`: [2](#0-1) 

This `get-asset-price` output feeds directly into `sum-collateral-usd` and `sum-debt-usd`, which are used to build `coll-usd` and `debt-usd`, which in turn compute `current-ltv`, `health-factor`, and `is-liquidatable`: [3](#0-2) [4](#0-3) 

Because BTC and STX prices diverge significantly and independently, substituting STX's price for BTC's price produces an arbitrarily wrong USD valuation for any account holding `stSTXbtc`/`zstSTXbtc` as collateral or debt — either overstating collateral value (letting under-collateralized positions look healthy, blocking legitimate liquidations) or understating debt value.

### Impact Explanation
If `health-factor`/`is-liquidatable`/`current-ltv` values produced by this contract are consumed on-chain to gate borrowing, withdrawals, or to determine liquidatability (rather than purely off-chain display), a wrong verdict directly enables borrowing against overstated collateral (theft/insolvency) or blocks correct liquidation of undercollateralized `stSTXbtc` positions (protocol insolvency / permanent freezing of the shortfall). This lands in the Critical impact bucket (protocol insolvency / theft of funds) if this path gates protocol actions, or High if it only affects liquidation timing for unclaimed value. I could not fully confirm within the available context whether `v0-1-data.clar`'s `get-account-health`-style function is called by the core `market.clar` borrow/liquidation contract-calls versus being a read-only reporting/UI contract; this should be verified, as it materially changes whether the impact is Critical (on-chain enforcement) or informational-only (no protocol impact).

### Likelihood Explanation
The bug triggers unconditionally whenever `stSTXbtc` or `zstSTXbtc` participates in a health/LTV computation through this function — no attacker action, oracle manipulation, or governance action is required; it is a deterministic wrong-feed substitution baked into the contract logic itself (not a registry misconfiguration, since the registry/oracle `ident` values for the actual market-price path in `market.clar` are separate from this hardcoded helper).

### Recommendation
Change line 582 (and the `zstSTXbtc` branch at line 585) in `mainnet/contracts/utility/v0-1-data.clar` to call `(get-pyth-price PYTH-BTC)` instead of `(get-pyth-price PYTH-STX)`, consistent with the `sBTC`/`zsBTC` branches and the inline comments.

### Proof of Concept
1. An account deposits `stSTXbtc` as collateral.
2. BTC price rises while STX price stays flat (or vice versa) — a routine market divergence, not manipulation.
3. `get-asset-price(stSTXbtc)` returns the STX/USD price instead of the true BTC-derived price for the asset.
4. `sum-collateral-usd`/`sum-debt-usd` compute `coll-usd`/`debt-usd` using this wrong price, producing an incorrect `current-ltv`/`health-factor`/`is-liquidatable` verdict for the account.
5. If any protocol action relies on this verdict, an account that should be liquidatable is reported healthy (or vice versa), leading to either delayed liquidation (bad debt accrual) or blocked legitimate borrowing/withdrawal for other users.

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L440-472)
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

**File:** mainnet/contracts/utility/v0-1-data.clar (L543-563)
```text
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
