Based on my research, I found a valid analog to the DODO `routeFeeRate` bug class within Zest's oracle confidence-gating logic.

### Title
Owner can set `max-confidence-ratio` to bypass Pyth confidence gating and admit low-confidence prices - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The DODO finding is about an owner-settable rate parameter with an upper bound that is technically enforced (`< 100%`) but effectively meaningless (up to 99.99%), letting a malicious/compromised owner push the parameter to the edge and cause severe user losses via a value that flows directly into a financial calculation. In Zest, the analogous settable parameter is `max-confidence-ratio`, which directly gates whether a Pyth price is trusted enough to be used in collateral/debt valuation and liquidation health checks.

### Finding Description
Zest's Pyth price resolution validates the reported confidence interval against a tolerance before accepting the price: [1](#0-0) 
`check-confidence` computes an allowed confidence band as `price * max-confidence-ratio / BPS` and asserts the reported `conf` is within it, otherwise rejecting with `ERR-PRICE-CONFIDENCE-LOW`. This value is then used unmodified as `final-price` in `resolve-pyth` and flows into `price-resolve`, which supplies the price used for every collateral/debt USD valuation and every health/liquidation verdict in `get-asset-value`, `is-healthy`, and `calc-liquidation-params`: [2](#0-1) [3](#0-2) 

`max-confidence-ratio` is a contract data variable read via `var-get`, implying it is settable by governance/owner. I was unable to locate the setter function definition and its bound check within the indexed portion of `v0-4-market.clar` (the index does not surface it), so I cannot confirm whether an upper-bound assertion exists on this setter — this is the key uncertainty in this analog, analogous to how DODO's `changeRouteFeeRate` had a check that was present but insufficient (`< 100%` instead of a sane cap like 10%).

If the setter permits `max-confidence-ratio` to be set arbitrarily close to `BPS` (100%), the confidence check becomes a no-op: prices with confidence intervals nearly as wide as the price itself would still pass `check-confidence`, meaning Pyth prices with extreme uncertainty (e.g., during a feed outage, thin liquidity, or oracle stress) get accepted as `final-price` and directly used to compute collateral/debt USD values and liquidation eligibility.

### Impact Explanation
A wrongly-accepted, highly uncertain price is used as ground truth for every position's collateral and debt USD valuation, and for `is-healthy` / liquidation threshold comparisons. If the true price deviates significantly from the accepted noisy price, this can (a) let undercollateralized positions be marked healthy, permitting excess borrowing and equity to walk out of the protocol, or (b) mark healthy positions as liquidatable, causing wrongful liquidation and theft of that collateral by liquidators. Both directions are direct theft/insolvency-adjacent impacts on funds at rest, landing in the Critical impact class (direct theft of user funds / protocol insolvency) if the ratio is pushed to its practical maximum.

### Likelihood Explanation
This requires the owner/governance-controlled `max-confidence-ratio` setter to lack a meaningful upper cap (analogous to DODO's `< 100%` check). Whether such a cap exists could not be confirmed from the indexed code available to me — the setter for `max-confidence-ratio` was not found in my search. If a tight cap is enforced elsewhere (e.g., a small fixed max like 5-10%), this finding would not apply. This is the primary gap in confidence for this analog.

### Recommendation
Introduce (or verify existence of) a hard-coded `MAX-CONFIDENCE-RATIO` constant (e.g., 200-500 bps, i.e., 2-5%) and assert any new value passed to the `max-confidence-ratio` setter is below it, rather than relying solely on `< BPS`, mirroring the DODO recommendation to cap `routeFeeRate` well below 100%.

### Proof of Concept
1. Owner/DAO calls the `max-confidence-ratio` setter with a value close to `BPS` (e.g., 9900/10000 = 99%).
2. Pyth reports a price during a stressed/illiquid period with `conf` close to `price` itself (extremely wide interval).
3. `check-confidence` at [1](#0-0)  passes because `conf <= price * 9900/10000`.
4. `resolve-pyth` returns this unreliable `final-price`, which `price-resolve` at [4](#0-3)  propagates into collateral/debt valuation.
5. Depending on which direction the true price deviates, either a borrower over-borrows against overstated collateral, or a healthy borrower is wrongfully liquidated.

**Note on confidence:** I could not locate the actual setter function/bound check for `max-confidence-ratio` in the indexed codebase, so this analog's validity hinges on that setter's implementation, which I was unable to verify. I recommend starting a Devin session with full repo access to confirm the setter's bound before treating this as confirmed.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L305-306)
```text
(define-private (check-confidence (price int) (confidence uint))
  (ok (asserts! (<= confidence (/ (* (to-uint price) (var-get max-confidence-ratio)) BPS)) ERR-PRICE-CONFIDENCE-LOW)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L312-320)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L656-687)
```text
(define-private (is-healthy (collateral-usd uint) (debt-usd uint) (ltv uint))
  (if (is-eq debt-usd u0)
      true
      (<= (* debt-usd BPS) (* collateral-usd ltv))))

;; Check health using a custom mask's egroup rules
;; Returns true if position is healthy under the specified mask's LTV requirements
(define-private (is-healthy-with-mask (collateral-usd uint) (debt-usd uint) (mask uint))
  (let ((group (try! (get-egroup mask)))
        (ltvb (buff-to-uint-be (get LTV-BORROW group))))
    (ok (is-healthy collateral-usd debt-usd ltvb))))

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
