### Title
Wrong price feed used for stSTXbtc / zstSTXbtc valuation (STX price applied instead of BTC price) - ([File: mainnet/contracts/utility/v0-1-data.clar])

### Summary
In the account/valuation utility contract, `get-asset-price` resolves the price of the `stSTXbtc` and `zstSTXbtc` asset IDs using the `PYTH-STX` feed constant instead of the `PYTH-BTC` feed, despite the inline comments explicitly stating "BTC price". This is a wrong-price-attached-to-wrong-asset bug rooted in this codebase (not third-party oracle data or DAO misconfiguration), matching the accepted analog class.

### Finding Description
`get-asset-price` in `v0-1-data.clar` is the shared pricing helper used to compute USD notional value for every registered asset ID, including `stSTXbtc`/`zstSTXbtc` (liquid-staked STX with BTC yield): [1](#0-0) 

```clarity
;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
(if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
;; zstSTXbtc - stSTXbtc price x liquidity index
(if (is-eq aid zstSTXbtc)
    (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
          (lindex (get-vault-liquidity-index stSTXbtc)))
      (mul-div-down btc-price lindex INDEX-PRECISION))
```

Compare this to the correct pattern used for `sBTC`/`zsBTC`, a few lines earlier in the same function, which correctly calls `PYTH-BTC`: [2](#0-1) [3](#0-2) 

The variable is even named `btc-price` in both `stSTXbtc` branches, but it is populated with `get-pyth-price PYTH-STX` — the STX/USD feed — not `PYTH-BTC`. Since STX/USD price is orders of magnitude lower than BTC/USD, every stSTXbtc/zstSTXbtc position is valued using the wrong (much cheaper) asset price. This `get-asset-price` function is consumed directly by the collateral/debt summing helpers in the same contract: [4](#0-3) [5](#0-4) 

### Impact Explanation
I confirmed the actual value-moving enforcement contract, `v0-4-market.clar`, does not use the hardcoded `PYTH-STX`/`PYTH-BTC` constants at all — it resolves prices dynamically via the registered per-asset `oracle` struct (`type`, `ident`, `callcode`) stored in the assets registry: [6](#0-5) 

I was not able to confirm, within the remaining exploration, whether `v0-1-data.clar`'s `get-asset-price`/`sum-collateral-usd`/`sum-debt-usd` chain feeds into any on-chain gating logic (e.g., a health-factor check that itself authorizes a state-changing action) versus being purely a read-only reporting/analytics helper consumed off-chain (frontend, monitoring, integrators). If it is purely informational, the practical protocol impact is limited to third parties (users, liquidator bots, integrators) receiving a materially wrong (understated) valuation for stSTXbtc/zstSTXbtc collateral, which could cause incorrect off-chain liquidation-risk assessments or borrowing-capacity displays, but would not by itself move funds since `v0-4-market.clar`'s own enforced pricing path is unaffected.

Given this uncertainty, I cannot confirm with the evidence gathered that this bug reaches an in-scope impact class (theft, insolvency, or freezing of funds enforced on-chain). Reporting/analytics-only pricing errors that don't gate an actual state-changing transaction fall under "no-impact" per the scan rules.

### Likelihood Explanation
The bug is deterministic and triggers on every call to `get-asset-price` for `aid stSTXbtc` or `aid zstSTXbtc` — no attacker action or special condition is required, it is a straightforward code defect. However likelihood of it having a real fund-impacting consequence depends on whether any state-changing contract path consumes this data-utility valuation, which I could not fully verify in-repo (`sum-collateral-usd`/`sum-debt-usd` usages were only found within `v0-1-data.clar` itself in my searches, suggesting it is likely a self-contained account-health reporting utility).

### Recommendation
Replace `(get-pyth-price PYTH-STX)` with `(get-pyth-price PYTH-BTC)` in both the `stSTXbtc` and `zstSTXbtc` branches of `get-asset-price` in `mainnet/contracts/utility/v0-1-data.clar` (lines 582 and 585), matching the pattern already used for `sBTC`/`zsBTC`. Additionally, confirm and document whether any on-chain state-changing function relies on this utility's valuation output; if so, add regression tests asserting `stSTXbtc` price tracks BTC/USD, not STX/USD.

### Proof of Concept
Call the read-only path that exercises `get-asset-price` for a position holding `stSTXbtc` or `zstSTXbtc` collateral (e.g., via `sum-collateral-usd`/account-health helpers in `v0-1-data.clar`) and compare the returned USD value against the correct BTC/USD-denominated value; the returned value will instead reflect the STX/USD price, understating true collateral value by the STX/BTC price ratio. [7](#0-6)

### Citations

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

**File:** mainnet/contracts/utility/v0-1-data.clar (L526-535)
```text
;; Helper: Sum debt USD values
(define-private (sum-debt-usd (entry { aid: uint, scaled: uint }) (acc uint))
  (let ((aid (get aid entry))
        (scaled (get scaled entry))
        (asset-data (unwrap-panic (contract-call? .v0-assets get-status aid)))
        (decimals (get decimals asset-data))
        (borrow-index (get-vault-borrow-index aid))
        (actual (mul-div-down scaled borrow-index INDEX-PRECISION))
        (price (get-asset-price aid)))
    (+ acc (/ (* actual price) (pow u10 decimals)))))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L540-589)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L332-358)
```text
(define-private (resolve-price-feed (type (buff 1)) (ident (buff 32)))
  (if (is-eq type TYPE-PYTH) (resolve-pyth ident)
  (if (is-eq type TYPE-DIA) (resolve-dia ident)
  ERR-ORACLE-TYPE)))

;; -- Oracle: callcode transformations ---------------------------------------

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
