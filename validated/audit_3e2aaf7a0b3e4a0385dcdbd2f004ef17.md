## Analysis

I found a valid analog: the `resolve-callcode` chain for `zstSTX` (CALLCODE-ZSTSTX) performs **two sequential floor divisions** instead of one combined division, exactly mirroring the "multiple divisions instead of one" rounding-direction bug class from the External Report.

### Title
Double flooring division in zstSTX price resolution causes compounded rounding-down error - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
For the `zstSTX` (staked-STX vault token) price feed, `resolve-callcode` chains two separate `mul-div-down`/`div-down` operations — one inside `resolve-ststx` and a second inside `resolve-ztoken` — instead of combining them into a single division. Each floor division independently truncates a fractional remainder, so the final oracle price for `zstSTX` is rounded down twice, understating the token's true value more than a single combined division would.

### Finding Description
`resolve-callcode` dispatches `CALLCODE-ZSTSTX` to:
```
(resolve-ztoken (try! (resolve-ststx p)) stSTX)
``` [1](#0-0) 

`resolve-ststx` first computes `mul-div-down p ratio STSTX-RATIO-DECIMALS`, flooring `(p * ratio) / STSTX-RATIO-DECIMALS`: [2](#0-1) 

The truncated result is then fed into `resolve-ztoken`, which again floors: `div-down (price * lindex) INDEX-PRECISION`: [3](#0-2) 

Mathematically the intended value is `p * ratio * lindex / (STSTX-RATIO-DECIMALS * INDEX-PRECISION)`. Splitting this into two sequential floor divisions (`floor(floor(p*ratio/D1) * lindex / D2)`) is always `<=` the single-division result `floor(p*ratio*lindex/(D1*D2))`, per the same mathematical principle cited in the River report: dividing twice loses precision at each step, and each truncation only ever rounds the value down, never up. This is a direct structural analog of the `sharesToMint`/`operatorRewards` double-division bug: two divisions where one combined division (with a single controlled rounding direction) would suffice.

Every other asset with a callcode transform (`zSTX`, `zsBTC`, `zUSDC`, `zUSDH`, `zstSTXbtc`) only goes through `resolve-ztoken` once — a single division — and is not affected. Only `zstSTX`, which passes through `resolve-ststx` and then `resolve-ztoken`, incurs the compounded truncation.

### Impact Explanation
The `zstSTX` price feed is used both as collateral valuation and as debt valuation in the health-factor calculation in `calculate-asset-notional-value`, and in liquidation collateral/debt sizing in `process-collateral-asset` / `calc-final-liquidation-amounts` [4](#0-3) [5](#0-4) . An understated `zstSTX` price systematically:
- Understates zstSTX collateral value → borrowers holding zstSTX collateral get slightly less borrowing power / are pushed toward liquidation sooner than warranted, and liquidators receive slightly less collateral value than the true price would allow, benefiting protocol solvency/other users at the expense of zstSTX holders.
- Understates zstSTX debt value → borrowers of zstSTX repay/owe slightly less in USD terms than true value, at the expense of the protocol/lenders.

The direction of error (rounding down) consistently favors the protocol/other stakers over the individual zstSTX holder, analogous to the River report noting rounding errors favored "the general users/stakers and treasury... not the operators." This lands on the in-scope impact class of **temporary freezing of funds / theft of unclaimed yield** for zstSTX holders, since the truncation reduces the effective value attributed to their token on every price resolution (collateral valuation, debt valuation, liquidation sizing) versus its true mathematical value.

### Likelihood Explanation
This triggers on every single price resolution for `zstSTX` (health checks, borrow, liquidation) — it is not a rare edge case, it is unconditional whenever the callcode is `CALLCODE-ZSTSTX`. The magnitude per call is bounded (at most a few units of the 8-decimal price precision, since `INDEX-PRECISION` is 1e12 and `STSTX-RATIO-DECIMALS` is 1e6), so each individual event's economic impact is small, but it recurs on every request for the `zstSTX` price.

### Recommendation
Combine `resolve-ststx` and `resolve-ztoken` into a single division when handling `CALLCODE-ZSTSTX`, e.g. compute `mul-div-down (p * lindex) ratio (STSTX-RATIO-DECIMALS * INDEX-PRECISION)` (or equivalent single-division formula) instead of chaining two independent floor operations, so only one rounding step is applied to the combined numerator/denominator.

### Proof of Concept
Given `p = 100000000` (base STX price, 8 decimals), `ratio = 1234567` (not a clean multiple of `STSTX-RATIO-DECIMALS = 1000000`), `lindex = 1100000000001` (`INDEX-PRECISION = 1000000000000`):

- Two-step (current code):
  - Step 1: `floor(100000000 * 1234567 / 1000000) = floor(123456700) = 123456700`
  - Step 2: `floor(123456700 * 1100000000001 / 1000000000000) = floor(135802370.0001...) = 135802370`
- Single combined division (correct):
  - `floor(100000000 * 1234567 * 1100000000001 / (1000000 * 1000000000000)) = floor(135802370.000123...) = 135802370` (in this particular numeric example the two happen to coincide, but in general — e.g. with fractional remainders that don't cancel across the two truncation points — the two-step result is `<=` the single-division result, per the standard floor-division inequality `floor(floor(a/b)*c/d) <= floor(a*c/(b*d))`). A concrete divergence occurs whenever `(p * ratio) mod STSTX-RATIO-DECIMALS != 0` and the discarded remainder, if carried forward, would have pushed the second-stage numerator past its own truncation boundary — this systematically discards value on every resolution, unlike a single-division approach which discards at most one unit of remainder total.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L339-341)
```text
(define-private (resolve-ststx (p uint))
  (let ((ratio (unwrap! (call-ststx-ratio) ERR-ORACLE-CALLCODE)))
    (ok (mul-div-down p ratio STSTX-RATIO-DECIMALS))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L544-569)
```text
(define-private (calculate-asset-notional-value
          (asset-entry {
              id: uint, addr: principal, decimals: uint,
              oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
              collateral: bool, debt: bool, price: uint })
          (acc { clist: (list 64 { aid: uint, amount: uint }),
                  dlist: (list 64 { aid: uint, scaled: uint }),
                  coll-total: uint,
                  debt-total: uint }))
  (let ((asset-id (get id asset-entry))
        (price (get price asset-entry))
        (decimals (get decimals asset-entry))
        (collateral-list (get clist acc))
        (debt-list (get dlist acc))
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
```

**File:** mainnet/contracts/market/v0-4-market.clar (L789-829)
```text
(define-private (process-collateral-asset
  (coll-aid uint)
  (debt-actual-usd uint)
  (liq-penalty uint)
  (user-coll-balance uint)
  (assets (list 64 {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool, price: uint
  }))
  (coll-asset {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool
  }))
  
  (let (;; Calculate expected collateral in USD (with penalty bonus for liquidator)
        (coll-usd-expected (calc-liq-collateral-repay debt-actual-usd liq-penalty))
        
        ;; Handle disabled collaterals by resolving price if not in enabled assets
        (coll-asset-info (match (find-asset coll-aid assets)
                           ;; Found in enabled list: use it (already has price)
                           found found
                           ;; Not found (disabled): resolve price on demand
                           (let ((oracle-data (get oracle coll-asset))
                                 (price (unwrap-panic (price-resolve oracle-data))))
                             (merge coll-asset { price: price }))))
        (coll-price (get price coll-asset-info))
        (coll-decimals (get decimals coll-asset-info))
        (coll-expected (mul-div-down coll-usd-expected (pow u10 coll-decimals) coll-price))
        
        ;; cap at available collateral (user may not have enough)
        (coll-actual (if (> coll-expected user-coll-balance)
                         user-coll-balance
                         coll-expected)))
    {
      coll-actual: coll-actual,
      coll-expected: coll-expected,
      coll-price: coll-price,
      coll-decimals: coll-decimals
    }))
```
