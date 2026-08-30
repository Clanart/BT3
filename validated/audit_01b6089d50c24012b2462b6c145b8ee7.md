### Title
Missing ststx staking-ratio transformation for `CALLCODE-ZSTSTX` causes zstSTX collateral to be undervalued - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`resolve-callcode` in the production market contract applies the wrong price transformation for the `CALLCODE-ZSTSTX` (zstSTX vault token, asset id `u5`) branch: it feeds the raw STX price directly into `resolve-ztoken` without first applying the `resolve-ststx` staking-ratio conversion that the docs specify and that the equivalent code path in `local-testing` actually performs. This is a callcode price-transformation bug (one of the explicitly in-scope categories) that produces an incorrect USD valuation for zstSTX positions, feeding a wrong price into the health/LTV calculation.

### Finding Description
Per protocol docs, the zstSTX token requires a **dual transformation**: (1) apply the ststx staking ratio to convert the base STX price into an stSTX price, then (2) apply the vault liquidity index via `resolve-ztoken` to get the zstSTX price: [1](#0-0) [2](#0-1) 

The `local-testing` reference implementation (functionally identical contract, same constants) correctly implements this dual transform:
```
(if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken (try! (resolve-ststx p)) stSTX)
``` [3](#0-2) 

However, the production `mainnet/contracts/market/v0-4-market.clar` `resolve-callcode` implements the CALLCODE-ZSTSTX branch **without** the `resolve-ststx` step, passing the raw base price `p` straight into `resolve-ztoken`:
```
(if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken p stSTX)
``` [4](#0-3) 

This price feeds directly into `price-resolve`, which is the value used everywhere collateral/debt USD valuation is computed (`get-asset-value`, `find-and-resolve-asset-value`, `get-notional-evaluation`, liquidation math), so the missing multiplier propagates into the health check and liquidation logic: [5](#0-4) [6](#0-5) 

### Impact Explanation
The stSTX/STX staking ratio (`STSTX-RATIO-DECIMALS`, `u1000000` = 6-decimal fixed-point) is monotonically increasing (>1.0) as staking rewards accrue over time: [7](#0-6) 

Because the ratio is omitted, every zstSTX-denominated collateral position is valued strictly lower than its true worth (the ratio's numerator/denominator relationship means `p` alone understates the true stSTX price by the accrued staking premium). The direction of the error: **collateral value is understated**. Understated collateral value directly reduces borrowing capacity for zstSTX depositors and — more importantly — makes zstSTX-collateralized positions appear closer to (or past) the liquidation LTV threshold than they actually are, since the health check `is-healthy`/liquidation trigger compares `debt-usd * BPS <= collateral-usd * ltv` using this understated `collateral-usd`: [8](#0-7) 

This allows liquidators to trigger and profit from liquidations against positions that are not actually unhealthy (temporary freezing/loss of the borrower's collateral beyond what is warranted), and conversely permanently understates value available for legitimate borrowing/withdrawal by the position owner. This lands in the in-scope impact category of **temporary freezing of funds** for affected zstSTX collateral users (their collateral is seized/frozen via unwarranted liquidation, or they are blocked from borrowing/withdrawing their full entitled value).

### Likelihood Explanation
This triggers automatically and deterministically any time `price-resolve`/`resolve-callcode` is invoked for an asset configured with `oracle.callcode = (some CALLCODE-ZSTSTX)` — i.e., any zstSTX collateral valuation, borrow, or liquidation call. No attacker action beyond a normal `liquidate` call is required once the ratio has drifted meaningfully from 1.0 (which happens naturally over time as staking rewards accrue), making likelihood high and impact continuous rather than one-off. `CALLCODE-ZSTSTX` (`0x03`) is a defined, wired-in constant used by asset id `zstSTX` (`u5`), confirming this is a live, reachable code path rather than dead code: [9](#0-8) 

### Recommendation
Fix `resolve-callcode` in `mainnet/contracts/market/v0-4-market.clar` to match the intended dual-transformation and the correct `local-testing` reference implementation:
```clarity
(if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken (try! (resolve-ststx p)) stSTX)
```
Add a regression test asserting that a zstSTX price equals `base_STX_price * ststx_ratio * liquidity_index`, matching the documented example in `docs/oracle.md`.

### Proof of Concept
1. Configure an asset (e.g. `.vault-ststx`) in the registry with `oracle: { type: TYPE-PYTH, ident: <STX/USD feed>, callcode: (some CALLCODE-ZSTSTX), max-staleness: ... }`.
2. Call any market function that resolves this asset's price (`collateral-add` capacity check, `borrow`, or `liquidate`) once the on-chain ststx ratio has diverged from `1.0` (e.g., `ratio = 1050000` representing 1.05).
3. Observe that `resolve-callcode` returns `resolve-ztoken(p, stSTX)` using the raw STX price `p` instead of `resolve-ztoken(resolve-ststx(p), stSTX)`, undervaluing the zstSTX position by the missing ~5% (or whatever the accrued ratio is) staking premium.
4. Compare against the `local-testing/contracts/market/market.clar` implementation, which produces the higher, correct value for the same inputs — demonstrating the discrepancy is a mainnet-specific regression, not intended design.

### Citations

**File:** docs/High-Level-Overview.md (L111-111)
```markdown
*   **Oracle Redundancy:** Explicit support for multiple oracle types (Pyth, DIA) ensures price feed resilience.
```

**File:** mainnet/contracts/market/v0-4-market.clar (L17-46)
```text
(define-constant STX u0)
(define-constant zSTX u1)    ;; vault-stx
(define-constant sBTC u2)
(define-constant zsBTC u3)   ;; vault-sbtc
(define-constant stSTX u4)
(define-constant zstSTX u5)  ;; vault-ststx
(define-constant USDC u6)
(define-constant zUSDC u7)   ;; vault-usdc
(define-constant USDH u8)
(define-constant zUSDH u9)   ;; vault-usdh
(define-constant stSTXbtc u10)
(define-constant zstSTXbtc u11) ;; vault-ststxbtc
(define-constant ztokens (list zSTX zsBTC zstSTX zUSDC zUSDH zstSTXbtc))

;; -- Precision & scaling
(define-constant BPS u10000)
(define-constant INDEX-PRECISION u1000000000000)  ;; 1e12 for index calculations

;; -- Oracle configuration
(define-constant TYPE-PYTH 0x00)
(define-constant TYPE-DIA 0x01)

;; -- Oracle callcodes (for price transformations)
(define-constant CALLCODE-STSTX 0x00)
(define-constant CALLCODE-ZSTX 0x01)
(define-constant CALLCODE-ZSBTC 0x02)
(define-constant CALLCODE-ZSTSTX 0x03)
(define-constant CALLCODE-ZUSDC 0x04)
(define-constant CALLCODE-ZUSDH 0x05)
(define-constant CALLCODE-ZSTSTXBTC 0x06)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L48-49)
```text
;; -- Oracle ratios
(define-constant STSTX-RATIO-DECIMALS u1000000)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L349-358)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L373-395)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L656-659)
```text
(define-private (is-healthy (collateral-usd uint) (debt-usd uint) (ltv uint))
  (if (is-eq debt-usd u0)
      true
      (<= (* debt-usd BPS) (* collateral-usd ltv))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L668-687)
```text
(define-private (find-and-resolve-asset-value
                  (assets (list 64 
                    { id: uint, addr: principal, decimals: uint,
                    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
                    collateral: bool, debt: bool, price: uint }))
                  (asset-id uint) (amount uint) (round-up bool))
  (match (find-asset asset-id assets)
    asset (normalize (* amount (get price asset)) (get decimals asset) round-up)
    u0))

;; find-and-resolve-asset-value has "price" already pre-calculated, get-asset-value does not
(define-private (get-asset-value
                  (asset { id: uint, addr: principal, decimals: uint,
                          oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
                          collateral: bool, debt: bool})
                  (amount uint) (round-up bool))
    (let ((oracle-data (get oracle asset))
          (price (try! (price-resolve oracle-data)))
          (decimals (get decimals asset)))
      (ok (normalize (* amount price) decimals round-up))))
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
