### Title
Pyth price normalization aborts on the standard 8-decimal exponent instead of using it, freezing all price-dependent market operations - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`normalize-pyth` in the mainnet market contract contains an incorrectly reversed edge-case check, structurally the same class of bug as the Maverick `Bin.sol#lpTokensFromDeltaReserve` finding: a boundary condition (`adj == 0`, the "no scaling needed" case) is handled with the wrong logic. Instead of taking a fast identity path when the Pyth exponent already matches the target 8 decimals, the code `asserts!`-aborts the entire call in exactly that case, even though the fallback arithmetic already computes the correct value for `adj == 0`.

### Finding Description
`normalize-pyth` converts a raw Pyth `(price, expo)` pair into an 8-decimal-normalized `uint`: [1](#0-0) 

```clarity
(define-private (normalize-pyth (p int) (expo int))
  (let ((adj (+ expo 8))
        (inkind? (asserts! (not (is-eq adj 0)) (to-uint p)))
        (res (if (> adj 0)
                (* p (pow 10 adj))
                (/ p (pow 10 (- adj))))))
    (to-uint res)))
```

`adj = expo + 8` is zero exactly when the Pyth feed already reports the price with the target 8-decimal exponent (`expo == -8`), which is the *standard* exponent used by Pyth crypto/USD feeds (STX/USD, BTC/USD, USDC/USD — the exact feeds registered in this protocol, see `PYTH-STX`, `PYTH-BTC`, `PYTH-USDC` in `mainnet/contracts/utility/v0-1-data.clar` lines 42-47). In that "in-kind" case the correct behavior is simply to return `p` unchanged — and the fallback arithmetic branch already does this correctly: `(/ p (pow 10 (- adj)))` with `adj = 0` reduces to `(/ p (pow 10 0))` = `(/ p 1)` = `p`.

However, the `inkind?` binding evaluates `(asserts! (not (is-eq adj 0)) (to-uint p))` *before* that arithmetic runs. `asserts!` aborts the entire enclosing public call whenever its boolean condition is false. The condition `(not (is-eq adj 0))` is false precisely when `adj == 0` — i.e., precisely in the normal, most common case. So whenever a registered Pyth feed reports the expected `expo = -8`, this function does not fall through to the (already-correct) identity computation; it instead reverts the whole transaction, using `(to-uint p)` as a nonsensical error payload.

This is the same bug shape as the analog report: an inequality/edge-case branch is inverted, so the code takes the wrong path (abort) precisely at the boundary (`adj == 0`) where the *other* branch already produces the right answer. Compare with the equivalent helper in `mainnet/contracts/utility/v0-1-data.clar`, which handles the identical boundary correctly without aborting: [2](#0-1) 

```clarity
(define-private (normalize-pyth (price int) (expo int))
  (let ((adj (+ expo 8)))
    (if (is-eq adj 0)
        (to-uint price)
        (if (> adj 0)
            (to-uint (* price (pow 10 adj)))
            (to-uint (/ price (pow 10 (- adj))))))))
```

The buggy `normalize-pyth` feeds directly into the market's live price-resolution path used for collateral/debt valuation, LTV/health checks, borrowing, and liquidation: [3](#0-2) 

```clarity
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

and `resolve-pyth`/`resolve-price-feed` feed `get-asset-value`, which is used throughout the market for USD valuation of collateral/debt: [4](#0-3) 

### Impact Explanation
Whenever a Pyth feed used by this market (`PYTH-STX`, `PYTH-BTC`, `PYTH-USDC`) reports its price with the expected `expo = -8` — the standard exponent for these crypto/USD feeds — any call chain that resolves that asset's price (borrow, withdraw against collateral, health checks, liquidation) reverts. This is not a third-party oracle data problem; the oracle is returning a normal, valid `(price, expo)` pair, but this contract's own normalization logic aborts on it. The result is a protocol-wide denial of service on price-dependent operations for affected assets — users cannot borrow, withdraw collateral past LTV checks, or have unhealthy positions liquidated while the feed's exponent sits at the standard value, i.e., temporary freezing of funds.

### Likelihood Explanation
High. This does not require any adversarial input — `expo = -8` is the default/expected exponent for the registered Pyth feeds, so this path can be triggered simply by whatever value Pyth publishes in normal operation, with no attacker action needed.

### Recommendation
Remove the `asserts!`-based `inkind?` short-circuit entirely, or restructure `normalize-pyth` to mirror the correct implementation already present in `mainnet/contracts/utility/v0-1-data.clar`: branch on `(is-eq adj 0)` to return `(to-uint p)` directly, and only otherwise apply the multiply/divide scaling — never abort on the in-kind case.

### Proof of Concept
1. A registered Pyth feed (e.g. `PYTH-STX`) publishes a price update with `expo = -8` (its normal exponent).
2. Any user action that needs this asset's USD price — e.g. `get-asset-value`/`find-and-resolve-asset-value` during borrow, withdraw, or health-check/liquidation flow — calls `resolve-price-feed` → `resolve-pyth` → `normalize-pyth price expo`.
3. Inside `normalize-pyth`, `adj = expo + 8 = 0`.
4. `(asserts! (not (is-eq adj 0)) (to-uint p))` evaluates `(not (is-eq 0 0))` = `false`, so `asserts!` aborts the entire transaction.
5. The borrow/withdraw/liquidate call reverts, even though the correct normalized price (`p` unchanged) was computable by the very next line's arithmetic. [1](#0-0)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L297-303)
```text
(define-private (normalize-pyth (p int) (expo int))
  (let ((adj (+ expo 8))
        (inkind? (asserts! (not (is-eq adj 0)) (to-uint p)))
        (res (if (> adj 0)
                (* p (pow 10 adj))
                (/ p (pow 10 (- adj))))))
    (to-uint res)))
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

**File:** mainnet/contracts/utility/v0-1-data.clar (L79-87)
```text
;; Normalize Pyth price to 8 decimal precision
;; Pyth returns price with negative exponent (e.g., price=12345, expo=-8 means $0.00012345)
(define-private (normalize-pyth (price int) (expo int))
  (let ((adj (+ expo 8)))
    (if (is-eq adj 0)
        (to-uint price)
        (if (> adj 0)
            (to-uint (* price (pow 10 adj)))
            (to-uint (/ price (pow 10 (- adj))))))))
```
