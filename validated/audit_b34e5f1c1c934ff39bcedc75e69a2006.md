## Title
Zest's zToken price callcode always rounds down, undermining the round-up protection meant for debt valuation - (File: `mainnet/contracts/market/v0-4-market.clar`)

## Summary
The bug report describes a rounding-direction defect in a share-accounting mechanism (`StWSX.sol`) where a single truncation direction applied consistently in one place produces a systematic accounting drift that favors one side. The same class of defect — a single, fixed rounding direction baked into a price/value transformation that is later reused for both a "round down for the protocol's benefit" path and a "round up for the protocol's benefit" path — is reachable in Zest's price-resolution pipeline for zTokens.

## Finding Description
Zest normalizes the notional value of collateral and debt with two different, deliberately chosen rounding directions in `calculate-asset-notional-value`: collateral value is rounded down and debt value is rounded up, both via the `normalize` helper. [1](#0-0) 

This directionality is only correctly protocol-conservative if the `price` value fed into both branches is itself unbiased. However, for zToken assets (zSTX, zsBTC, zstSTX, zUSDC, zUSDH, zstSTXbtc), the price is produced once by `resolve-callcode` → `resolve-ztoken`, which applies the liquidity index using a hardcoded downward rounding (`div-down`) regardless of whether the caller will use the result to value collateral or debt: [2](#0-1) 

`price-resolve` computes and caches this single truncated price per asset per block, and that same cached value is subsequently consumed for both the collateral leg (round-down, correct) and the debt leg (round-up, but on an already-truncated-down price) in `calculate-asset-notional-value`: [3](#0-2) 

Because the truncation happens upstream of the debt-value rounding, the "round up" applied by `normalize` on the debt path only rounds up the already-shrunk value — it cannot recover the precision lost when `resolve-ztoken` truncated the liquidity-index multiplication downward. This mirrors the reported class exactly: a value derived through share/index math with a single fixed rounding direction is reused in a context that needs the opposite direction to stay conservative, producing a persistent, compounding understatement.

## Impact Explanation
For any position holding a zToken as debt (borrowed zToken), the debt notional used in health/liquidation checks is systematically understated relative to true economic value, because the underlying zToken price itself was already rounded down before the "round-up" debt normalization is applied. This understatement:
- Lets a borrower's health factor appear better than it actually is, allowing borrowing slightly beyond the true safe limit.
- Can delay or prevent liquidation of an unhealthy position by a few wei-equivalent USD units per accrual, which grows as the liquidity index and deposit balances grow (exactly as flagged in the original report as "small now, but increasing over time").

This lands in the **temporary freezing of funds / theft of unclaimed yield** category in spirit — protocol solvency is chipped away at wei-scale per position, growing with usage, favoring borrowers over the protocol/lenders. It does not enable an immediate large drain, so it is bounded rather than critical.

## Likelihood Explanation
This triggers on every price resolution for any zToken-denominated debt position under normal operation — no attacker action or configuration flaw is required, only zToken debt to exist and the liquidity index to be non-trivial (which it always eventually is). The magnitude per call is small (sub-unit rounding), matching the audit report's own characterization of the analogous bug as a persistent but currently small drift.

## Recommendation
Push the rounding decision to the point of use rather than baking a single direction into `resolve-ztoken`/`resolve-ststx`. Provide a `round-up` parameter to these callcode resolvers (mirroring `normalize`'s `round-up` parameter) so debt-path price resolution can round up and collateral-path price resolution can round down, preserving the protocol-conservative invariant end-to-end instead of losing precision upstream.

## Proof of Concept
1. A zToken (e.g., zUSDC) accrues a liquidity index `cached-lindex` such that `p * cached-lindex` is not evenly divisible by `INDEX-PRECISION`.
2. `resolve-ztoken` truncates this via `div-down`, losing up to `INDEX-PRECISION - 1` units of precision in the price before it is cached in `price-resolve`. [4](#0-3) 
3. A user with borrowed zUSDC has their debt notional computed via `calculate-asset-notional-value`, which multiplies `actual` (scaled debt) by this already-truncated `price`, then rounds up only the final division by `decimal-factor` — never recovering the earlier truncation. [5](#0-4) 
4. Over many blocks/positions, the aggregate understatement of debt value grows, letting borrowers sit fractionally under-collateralized relative to the protocol's intended conservative accounting.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L339-358)
```text
(define-private (resolve-ststx (p uint))
  (let ((ratio (unwrap! (call-ststx-ratio) ERR-ORACLE-CALLCODE)))
    (ok (mul-div-down p ratio STSTX-RATIO-DECIMALS))))

(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))

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

**File:** mainnet/contracts/market/v0-4-market.clar (L558-580)
```text
        (coll-amount (find-collateral-amount collateral-list asset-id))
        (coll-notional (if (> coll-amount u0)
                           (normalize (* coll-amount price) decimals false)
                           u0))

        (debt-scaled   (find-debt-scaled debt-list asset-id))
        (debt-notional (if (> debt-scaled u0) ;; use cache instead here
                           (let ((cached (unwrap-panic (accrue-and-cache asset-id)))
                                 (ib (get index cached))
                                 (actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                             (normalize (* actual price) decimals true))
                           u0)))

    { clist: collateral-list,
      dlist: debt-list,
      coll-total: (+ (get coll-total acc) coll-notional),
      debt-total: (+ (get debt-total acc) debt-notional) }))

(define-private (normalize (value uint) (decimals uint) (round-up bool))
  (let ((decimal-factor (pow u10 decimals)))
    (if round-up
      (div-up value decimal-factor)
      (div-down value decimal-factor))))
```
