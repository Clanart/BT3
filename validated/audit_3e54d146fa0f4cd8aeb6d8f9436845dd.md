## Title
Missing ststx-ratio transformation in `resolve-callcode` for `CALLCODE-ZSTSTX` causes zstSTX collateral/debt to be mispriced - (File: `mainnet/contracts/market/v0-4-market.clar`)

## Summary
The docs (`docs/oracle.md`) and the newer `local-testing/contracts/market/market.clar` both specify that the `zstSTX` vault-token price requires a **dual transformation**: first apply the `ststx` staking ratio, then apply the vault liquidity index. In the in-scope production file `mainnet/contracts/market/v0-4-market.clar`, the `resolve-callcode` branch for `CALLCODE-ZSTSTX` skips the ratio step entirely and feeds the raw STX price directly into `resolve-ztoken`.

## Finding Description
`resolve-callcode` dispatches oracle post-processing by callcode byte: [1](#0-0) 

```clarity
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
    (if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken p stSTX)
    ...
```

Compare against the newer/reference implementation in `local-testing/contracts/market/market.clar`, which correctly composes both transforms for the same callcode: [2](#0-1) 

```clarity
(if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken (try! (resolve-ststx p)) stSTX)
```

The documentation confirms this is the intended design, explicitly calling out zstSTX as a "Special Case" requiring dual transformation ("Apply ststx staking ratio" then "Apply liquidity index").

In the deployed production contract, `CALLCODE-ZSTSTX` calls `resolve-ztoken p stSTX` directly on the raw underlying price feed (STX/USD from Pyth) without first applying `resolve-ststx`. This is exactly the class of bug the rules flag as in-scope: a **callcode transform** step is dropped, producing a wrong price attached to the zstSTX asset.

## Impact Explanation
`zstSTX` is a lending-market collateral/debt asset (`zstSTX`, aid `u5`) whose USD value feeds directly into the market's notional/health calculations (`calculate-asset-notional-value`, `get-notional-evaluation`, `is-healthy`, and the liquidation path in `liquidate`). Since `stSTX` accrues value relative to STX over time via staking rewards (`ststx-ratio > 1`), omitting the ratio multiplication causes the market to systematically **undervalue** zstSTX collateral and **undervalue** zstSTX debt by the missing ratio factor.

- **Collateral undervaluation**: users holding zstSTX as collateral get less borrowing power than they should (a self-limiting mispricing, not directly exploitable for theft), but also causes such positions to be liquidated/flagged unhealthy earlier than warranted — a false-liquidation risk that can permanently freeze/lose value for the borrower via improper liquidation.
- **Debt undervaluation**: if zstSTX is borrowed as debt, its true USD debt is understated, letting a borrower under-collateralize relative to real value; a liquidator/protocol receives less than the true debt owed on liquidation, and other users' collateral could ultimately be shorted (insolvency risk) once the ratio gap widens (ststx-ratio grows monotonically with staking yield).

Either direction ends at a **wrong price feeding into the on-chain health/LTV verdict**, matching the "wrong price / wrong health verdict" categories the rules require (specifically the "callcode transform" analog class). This lands in the **High** impact bucket (temporary/permanent freezing of funds / theft of unclaimed yield) or **Critical** (protocol insolvency) depending on how large the ststx ratio drift becomes over the position's lifetime, since the error compounds as ststx-ratio increases from its baseline.

## Likelihood Explanation
This triggers on every price resolution for any position holding zstSTX as collateral or debt — i.e., every borrow, withdraw, health check, and liquidation call that touches the zstSTX asset. It requires no attacker action beyond normal usage of the zstSTX market; the ststx-ratio simply needs to differ from 1 (which it does as soon as stSTX has accrued any staking rewards versus 1:1 STX). Likelihood is therefore high once the zstSTX vault has any material staking yield accrued.

## Recommendation
In `mainnet/contracts/market/v0-4-market.clar`, change the `CALLCODE-ZSTSTX` branch of `resolve-callcode` to compose both transforms, matching the documented design and the corrected `local-testing` implementation:
```clarity
(if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken (try! (resolve-ststx p)) stSTX)
```

## Proof of Concept
1. Assume `stSTX` price feed returns the underlying STX/USD price (per asset registration in `docs/oracle.md`, the `.ststx` asset itself uses `CALLCODE-STSTX` for direct stSTX pricing, but `.vault-ststx` (zstSTX) is registered with `CALLCODE-ZSTSTX` and the same base STX/USD `ident`).
2. `call-ststx-ratio()` returns, e.g., `1050000` (ratio 1.05, `STSTX-RATIO-DECIMALS = 1000000`), reflecting 1 stSTX = 1.05 STX from staking accrual.
3. On mainnet, `resolve-callcode` for zstSTX computes `resolve-ztoken(p, stSTX)` = `p * lindex / INDEX-PRECISION` using the raw STX price `p`, never multiplying by the `1.05` ratio.
4. The correct value should be `resolve-ztoken(resolve-ststx(p), stSTX)` = `(p * 1.05) * lindex / INDEX-PRECISION`.
5. Every downstream USD notional computed for a zstSTX collateral/debt position (`calculate-asset-notional-value`) is therefore off by the missing `1.05x` (or whatever the current ratio is) factor, directly skewing `current-ltv`, `is-healthy`, and liquidation eligibility/amounts for all zstSTX positions.

**Note on confidence**: I was unable to fully confirm from the index which oracle `ident`/`type` is actually registered on-chain for the `.vault-ststx` asset in `mainnet/contracts/proposals/mainnet/v0-init.clar` (the file is large and truncated in the tool output before I could inspect the exact `insert` call for `zstSTX`/`vault-ststx`), so I cannot 100% rule out that mainnet's registration compensates by pointing the `zstSTX` callcode configuration differently than local-testing does. Given the direct code-vs-code discrepancy in `resolve-callcode` and the explicit documentation stating zstSTX needs dual transformation, this is a strong analog to the report's "callcode transform" bug class, but full confirmation would require inspecting the exact asset registration entry, which the current index did not fully surface. If verification of the exact oracle config for zstSTX in `v0-init.clar` is needed, a deeper session against the full repository content would be required.

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
