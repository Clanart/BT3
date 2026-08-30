### Title
Zero-rounding of the zToken/stSTX price callcode transform systematically undervalues debt (never collateral) - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
### Finding Description
Zest's price pipeline resolves a raw oracle price and then applies a "callcode" transform before the price is used to value both collateral and debt positions of a user. Two of the callcode transforms always round the resulting price down, unconditionally, with no `round-up` parameter threaded through: [1](#0-0) 

`resolve-ststx` applies `mul-div-down` to convert the STX price into an stSTX price via the staking ratio, and `resolve-ztoken` applies `div-down` to convert a base price into a zToken price via the cached liquidity index. Both are floor-rounding operations with no ceiling variant.

This transformed price is the single `final-price` returned by `price-resolve` and fed identically into both collateral valuation and debt valuation: [2](#0-1) 

Downstream, `get-asset-value`/`find-and-resolve-asset-value` do carry an explicit `round-up` flag through `normalize` for the final amount×price scaling step, but that flag only affects the decimals-normalization step, not the price value itself, which is already floor-rounded by `resolve-callcode`/`resolve-ztoken`/`resolve-ststx` before it ever reaches `normalize`: [3](#0-2) 

The oracle system explicitly supports zToken-denominated debt assets — zUSDC, zUSDH, zSTX, zSBTC, zSTSTXBTC — each configured with `callcode: (some CALLCODE-Z*)`: [4](#0-3) 

For an asset marked `debt: true`, the correct rounding direction for a conservative (protocol-safe) valuation is to round the *debt* price **up** (so a borrower's owed value is never underestimated), while collateral price should round **down**. Because `resolve-ztoken`/`resolve-ststx` always floor the price regardless of whether the asset entry is being resolved as collateral or as debt, every debt valuation that passes through a zToken or stSTX callcode is silently biased low.

### Impact Explanation
Debt value derived from `price-resolve` feeds directly into the liquidation/health-factor math (`process-collateral-asset`, `calc-liq-factor`, `calc-liq-debt-repay`, etc., all consuming asset values produced from this price). A systematically undervalued debt price means a borrower's real debt-to-collateral ratio is always reported slightly better than reality. This is the same rounding-direction bug class as the referenced report (favoring the debtor at the expense of the protocol/other users), but here it manifests as a chronic under-accounting of debt/interest owed rather than a single zeroed withdrawal. Repeated interactions (borrow/repay/liquidation triggers, each re-invoking `price-resolve`) compound this bias over the life of a position, permanently understating value the protocol is owed — falling under the "theft of unclaimed yield" / value-owed-to-protocol impact class.

### Likelihood Explanation
This triggers on every price resolution for any asset configured with `CALLCODE-STSTX`, `CALLCODE-ZSTX`, `CALLCODE-ZSBTC`, `CALLCODE-ZSTSTX`, `CALLCODE-ZUSDC`, `CALLCODE-ZUSDH`, or `CALLCODE-ZSTSTXBTC` — i.e., it is not a rare edge case but the default path for any zToken or stSTX debt asset, occurring on ordinary borrow/repay/health-check calls with no attacker action required to trigger the rounding, only to benefit from it by holding debt in one of these assets.

### Recommendation
Thread a `round-up` boolean into `resolve-callcode`/`resolve-ztoken`/`resolve-ststx` (as is already done for `normalize`), and round the transformed price up when the asset is being valued as debt, down when valued as collateral, mirroring the existing round-up-for-debt/round-down-for-collateral convention used elsewhere in the codebase.

### Proof of Concept
Given a zUSDC debt position priced through `resolve-ztoken`: each call computes `scaled = p * cached-lindex` then `div-down scaled INDEX-PRECISION`, discarding the remainder every time. Over many index-accrual cycles (interest accrual increments `lindex`, and every borrow/repay/liquidation call re-resolves price), the cumulative discarded remainder represents debt value the borrower effectively never gets charged for, unlike collateral value from the same function which is *supposed* to be floored (favoring the protocol) — but here debt gets the same treatment, which is the wrong direction for debt.

Note: I was not able to fully verify, within the available context, whether `find-and-resolve-asset-value`/`get-asset-value` in the mainnet `v0-4-market.clar` (as opposed to the `local-testing/contracts/market/market.clar` reference cited) thread any additional debt-specific rounding correction elsewhere in the health-factor pipeline; if such a correction exists downstream, it would mitigate but not eliminate this issue at the price-resolution layer itself.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L339-347)
```text
(define-private (resolve-ststx (p uint))
  (let ((ratio (unwrap! (call-ststx-ratio) ERR-ORACLE-CALLCODE)))
    (ok (mul-div-down p ratio STSTX-RATIO-DECIMALS))))

(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
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

**File:** local-testing/contracts/market/market.clar (L691-709)
```text
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
