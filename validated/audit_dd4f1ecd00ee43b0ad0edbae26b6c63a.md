### Title
Missing minimum bound in `set-max-confidence-ratio` allows DAO to permanently block all Pyth price resolution, freezing borrows, withdrawals, and liquidations - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`set-max-confidence-ratio` only enforces an upper bound (`<= BPS`) on the confidence-ratio parameter, with no lower bound. Setting it to `0` (or any value low enough) makes `check-confidence` reject every legitimate Pyth price update, causing `price-resolve`/`resolve-pyth` to revert with `ERR-PRICE-CONFIDENCE-LOW` for every asset priced by Pyth (STX, sBTC, USDC), which blocks collateral valuation, borrowing, withdrawals, and — critically — liquidations across the whole market.

### Finding Description
`set-max-confidence-ratio` is defined as: [1](#0-0) 

It only checks `(<= ratio BPS)` and never enforces `ratio > 0` or any sane minimum. This value directly parameterizes `check-confidence`, which every Pyth price resolution must pass: [2](#0-1) 

`check-confidence` asserts `confidence <= price * max-confidence-ratio / BPS`. If `max-confidence-ratio` is `0`, the right-hand side evaluates to `0`, so the assertion `confidence <= 0` fails for any Pyth feed reporting a nonzero confidence interval (which is the normal case), causing `resolve-pyth` — and therefore `price-resolve` — to revert with `ERR-PRICE-CONFIDENCE-LOW` for every Pyth-priced asset. Since `resolve-price-feed` routes STX, sBTC, and USDC through `resolve-pyth`: [3](#0-2) 

...any operation that needs to price these assets — `get-asset-value`, `get-notional-evaluation`, collateral add/remove, borrow, repay, and liquidation flows that resolve prices on demand — will revert as soon as it touches an affected feed: [4](#0-3) [5](#0-4) 

This is the same bug class as the reported `setAuctionDecrement` issue: an owner/DAO-controlled setter checks one side of a bound but omits the other, and the unchecked extreme value causes a downstream, security-critical function (here, price resolution feeding into liquidation health checks) to revert unconditionally rather than operate.

### Impact Explanation
With `max-confidence-ratio` set to `0`, all Pyth-priced collateral/debt cannot be valued, so `settle`-equivalent operations in Zest — liquidations, borrows, withdrawals — revert. Liquidators cannot liquidate undercollateralized positions during this window, and depositors cannot manage their positions, constituting a temporary freezing of funds across the protocol until the parameter is corrected.

### Likelihood Explanation
This requires the DAO-authorized caller (`check-dao-auth`) to call `set-max-confidence-ratio` with a value at or near `0`, which is possible either through a misconfiguration/fat-fingered governance action or a compromised/erroneous multisig transaction — there is no on-chain protection preventing it, unlike similar parameters elsewhere in the codebase (e.g., egroup LTV validation which enforces strict ordering). Because the check exists but is incomplete (only an upper bound), this is a straightforward oversight rather than a hypothetical.

### Recommendation
Add a lower bound to `set-max-confidence-ratio`, e.g.:
```clarity
(define-public (set-max-confidence-ratio (ratio uint))
  (begin
    (try! (check-dao-auth))
    (asserts! (and (> ratio u0) (<= ratio BPS)) ERR-ORACLE-INVARIANT)
    ...
```
Choose a sane non-zero minimum reflecting the smallest confidence tolerance the protocol intends to support, so that legitimate Pyth confidence intervals can never be unconditionally rejected.

### Proof of Concept
1. DAO calls `set-max-confidence-ratio(0)` on `v0-4-market.clar` (only the `<= BPS` check applies, so this succeeds).
2. Any subsequent call touching a Pyth-priced asset (e.g., `collateral-add`, `borrow`, or a liquidation flow that calls `price-resolve`/`get-asset-value` for STX/sBTC/USDC) invokes `resolve-pyth` → `check-confidence`.
3. Since real Pyth feeds report `conf > 0`, and `(/ (* price 0) BPS) = 0`, the assertion `confidence <= 0` fails, and the transaction reverts with `ERR-PRICE-CONFIDENCE-LOW`.
4. All borrow, withdraw, and liquidation operations relying on these feeds are blocked until the DAO corrects `max-confidence-ratio`.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L332-335)
```text
(define-private (resolve-price-feed (type (buff 1)) (ident (buff 32)))
  (if (is-eq type TYPE-PYTH) (resolve-pyth ident)
  (if (is-eq type TYPE-DIA) (resolve-dia ident)
  ERR-ORACLE-TYPE)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L679-687)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L811-815)
```text
                           found found
                           ;; Not found (disabled): resolve price on demand
                           (let ((oracle-data (get oracle coll-asset))
                                 (price (unwrap-panic (price-resolve oracle-data))))
                             (merge coll-asset { price: price }))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L995-1010)
```text
(define-public (set-max-confidence-ratio (ratio uint))
  (begin
    (try! (check-dao-auth))
    (asserts! (<= ratio BPS) ERR-ORACLE-INVARIANT)
    
    (print {
      action: "market-set-max-confidence-ratio",
      caller: tx-sender,
      data: {
        old-value: (var-get max-confidence-ratio),
        new-value: ratio
      }
    })
    
    (var-set max-confidence-ratio ratio)
    (ok true)))
```
