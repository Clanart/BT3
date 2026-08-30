### Title
`protocol-data`/`v0-1-data` health/price helpers omit staleness and confidence checks present in the core market oracle path, producing a wrong health verdict for integrators - ([File: mainnet/contracts/utility/v0-1-data.clar])

### Summary
The `mainnet/contracts/utility/v0-1-data.clar` contract (mirrored by `local-testing/contracts/utility/protocol-data.clar`) exposes `get-user-position`, which computes and returns `health-factor` and `is-liquidatable` for any account. Internally it prices every asset via `get-asset-price` → `get-pyth-price`/`get-dia-price`, which call the Pyth/DIA storage contracts directly and skip the staleness (`oracle-timestamp-fresh`/`max-staleness`) and confidence (`check-confidence`) checks that the core lending logic in `mainnet/contracts/market/v0-4-market.clar` enforces via `price-resolve`. Any integrator (liquidation bot, partner protocol, dashboard) that trusts this contract's `health-factor`/`is-liquidatable` fields as ground truth can be given a verdict computed from an arbitrarily stale or low-confidence price feed, diverging from what the protected market contract would actually allow.

### Finding Description
`v0-1-data.clar`'s `get-pyth-price` reads the Pyth feed and normalizes the price, but performs no freshness or confidence validation: [1](#0-0) 

Compare this to the protected path used for actual protocol enforcement in the market contract, which explicitly checks confidence and enforces per-feed staleness (`max-staleness`) and monotonic timestamps before any price is used for borrow/liquidation decisions: [2](#0-1) [3](#0-2) 

`get-asset-price` (used by `get-user-position`) routes every asset price through the unguarded `get-pyth-price`/`get-dia-price` helpers, never through `price-resolve`: [4](#0-3) 

`get-user-position` then uses this unguarded price to compute the externally consumed health verdict: [5](#0-4) 

This mirrors the reported bug class: a price/health-reporting function omits the metadata/validation (staleness, confidence) that a careful integrator needs, and that the protocol's own core logic already implements elsewhere, so the externally exposed verdict silently diverges from the protocol's actual, protected state.

### Impact Explanation
Any third party (liquidation bots, partner lending/insurance protocols, risk dashboards) that treats `get-user-position`'s `health-factor`/`is-liquidatable` as authoritative can be misled:
- If the Pyth/DIA feed is stale or outside confidence bounds, `get-user-position` still returns a computed, seemingly valid `is-liquidatable`/`health-factor`, whereas `v0-4-market.clar`'s own `price-resolve` would revert with `ERR-ORACLE-INVARIANT`/`ERR-PRICE-CONFIDENCE-LOW` for the same feed.
- A partner protocol composing on top of Zest and using this data as a price/health oracle can extend credit or skip liquidations based on stale collateral valuations, risking its own insolvency or delayed recovery of at-risk collateral (temporary freezing of funds for positions that should have been actioned on).

### Likelihood Explanation
This triggers under normal, expected conditions — any time the Pyth/DIA feed underlying an asset goes stale (network congestion, relayer downtime) or its confidence interval widens, which is a routine occurrence for push/pull oracle feeds. No attacker action or DAO/registry misconfiguration is required; the omission is purely in this contract's own code path.

### Recommendation
Route `get-asset-price`/`get-user-position` in `v0-1-data.clar` through the same `price-resolve`/`check-confidence`/`oracle-timestamp-fresh` validation used by `v0-4-market.clar`, or otherwise surface the underlying feed's `publish-time`/confidence alongside the computed health data so integrators can independently verify freshness before trusting the verdict.

### Proof of Concept
1. Let a Pyth price feed (e.g., `PYTH-STX`) go stale beyond `max-staleness` recorded in `assets.clar` for that asset, while still present in `pyth-storage-v4`'s map.
2. Call `v0-4-market.clar`'s liquidation/borrow path for a position using that asset: `price-resolve` invokes `oracle-timestamp-fresh` and reverts with `ERR-ORACLE-INVARIANT` since `stacks-block-time - timestamp > max-staleness`.
3. Call `v0-1-data.get-user-position` for the same account: `get-asset-price` → `get-pyth-price` returns the stale price with no freshness check, so `health-factor`/`is-liquidatable` are computed and returned successfully — diverging from step 2's protected behavior and misleading any integrator relying on this read function.

### Citations

**File:** local-testing/contracts/utility/protocol-data.clar (L94-100)
```text
;; Get price from Pyth oracle storage (read-only)
;; Returns price in 8 decimal precision (e.g., $1.00 = 100000000)
(define-private (get-pyth-price (feed-id (buff 32)))
  ;; @mainnet: (match (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4 get-price feed-id)
  (match (contract-call? .pyth-storage-v4 get-price feed-id)
    result (some (normalize-pyth (get price result) (get expo result)))
    err-val none))
```

**File:** local-testing/contracts/utility/protocol-data.clar (L441-494)
```text
(define-read-only (get-user-position (account principal))
  (let ((enabled-mask (contract-call? .assets get-bitmap)))
    (match (contract-call? .market-vault get-position account enabled-mask)
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
              (egroup-result (contract-call? .egroup resolve mask)))
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

**File:** local-testing/contracts/utility/protocol-data.clar (L544-597)
```text
;; Helper: Get asset price from oracles
;; Returns price in 8 decimal precision (e.g., $1.00 = 100000000)
;; Handles all asset types: underlying, stSTX (with ratio), and zTokens (with liquidity index)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L305-320)
```text
(define-private (check-confidence (price int) (confidence uint))
  (ok (asserts! (<= confidence (/ (* (to-uint price) (var-get max-confidence-ratio)) BPS)) ERR-PRICE-CONFIDENCE-LOW)))

(define-private (call-pyth (ident (buff 32)))
  (let ((res (unwrap! (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4 get-price ident) ERR-ORACLE-PYTH)))
    (ok res)))

(define-private (resolve-pyth (ident (buff 32)))
  (let ((response (try! (call-pyth ident)))
        (price (get price response))
        (expo (get expo response))
        (conf (get conf response))
        (final-price (normalize-pyth price expo))
        (timestamp (get publish-time response)))
    (try! (check-confidence price conf))
    (ok { value: final-price, timestamp: timestamp })))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L362-395)
```text
(define-private (oracle-price-legal (p uint))
  (> p u0))

(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))

(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let ((type (get type data))
        (ident (get ident data))
        (key { type: type, ident: ident })
        (resolution (try! (resolve-price-feed type ident)))
        (price (get value resolution))
        (callcode (get callcode data))
        (final-price (try! (resolve-callcode price callcode)))
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))

    ;; validate price and timestamp using max-staleness from oracle data
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)

    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)

    (ok final-price)))
```
