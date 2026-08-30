Confirmed: this is a "read-only utility contract" per its own header comment, separate from the actual market pricing logic in `v0-4-market.clar`'s `price-resolve`/`resolve-price-feed` functions, which pull oracle config (`type`, `ident`, `callcode`) dynamically from the asset registry rather than through this hardcoded per-asset switch. I was not able to fully confirm within the available iterations whether `get-asset-price` in this utility contract is ever called by the real solvency/liquidation path (`v0-4-market.clar`, `v0-market-vault.clar`) versus being purely a display/query helper for front-ends. That distinction is decisive for impact and I could not verify it with certainty.

### Title
`stSTXbtc`/`zstSTXbtc` priced against the STX/USD feed instead of BTC/USD - (File: `mainnet/contracts/utility/v0-1-data.clar`)

### Summary
In `get-asset-price`, the branches for `stSTXbtc` and `zstSTXbtc` call `get-pyth-price PYTH-STX` (the STX/USD feed) while every comment, variable name (`btc-price`), and the asset's economic nature (a BTC-yield-bearing staked-STX wrapper) indicate the BTC/USD feed (`PYTH-BTC`) should be used.

### Finding Description
`get-asset-price` resolves USD prices per asset ID for use in `sum-collateral-usd` and `sum-debt-usd`: [1](#0-0) 
Every other asset in the same function correctly matches its comment/label to its feed constant (e.g. `sBTC` → `PYTH-BTC` [2](#0-1) , `USDC` → `PYTH-USDC` [3](#0-2) ). Only `stSTXbtc`/`zstSTXbtc` diverge, using `PYTH-STX` where `PYTH-BTC` is named in the comment and variable. This is the "price attached to the wrong asset" bug class, not a third-party oracle data issue or registry misconfiguration — the feed identifier is hardcoded in this contract's own conditional logic.

### Impact Explanation
Direction of error: `stSTXbtc`/`zstSTXbtc` USD value is computed from the STX/USD price rather than BTC/USD. Since BTC's USD price is far higher than STX's, this collateral/debt is grossly **undervalued** relative to its real worth wherever this function's output feeds into collateral/debt USD sums. If this pricing were used to gate borrowing/liquidation for `stSTXbtc`/`zstSTXbtc` positions, an attacker holding this asset as debt could appear to owe far less USD than actually borrowed (protocol insolvency / theft of protocol funds), while collateral positions would appear far less valuable than in reality (harmless to the protocol, only to the depositor). However, I could not confirm within the available tool budget whether this utility contract's `get-asset-price`/`sum-collateral-usd`/`sum-debt-usd` are wired into the actual solvency/liquidation enforcement path in `v0-4-market.clar`/`v0-market-vault.clar`, versus being purely informational/read-only (its own file header explicitly calls it a "read-only utility contract" [4](#0-3) ). If it is read-only/display-only, this has no on-chain fund-safety impact and should be downgraded to informational.

### Likelihood Explanation
No privileged access is required to trigger this — any user holding or borrowing `stSTXbtc`/`zstSTXbtc` and any caller of the affected read function would be given this bad price. If this were wired to enforcement, it would be automatically and continuously reachable.

### Recommendation
Replace `PYTH-STX` with `PYTH-BTC` in the `stSTXbtc` and `zstSTXbtc` branches of `get-asset-price` in `mainnet/contracts/utility/v0-1-data.clar`, and audit whether this same mis-mapping exists anywhere else (e.g. is duplicated in `v0-4-market.clar`'s hardcoded ID list, though that file resolves prices dynamically through `price-resolve`/asset-registry oracle config rather than this switch, per [5](#0-4) , so it may not share this specific bug). Confirm and document whether `v0-1-data.clar` output feeds into any solvency/liquidation decision.

### Proof of Concept
1. Call the read-only path that resolves `get-asset-price` for `aid = stSTXbtc` (u10) or `aid = zstSTXbtc` (u11).
2. Observe the returned USD price equals the current STX/USD Pyth price rather than a BTC-denominated value.
3. Compare against the correct behavior for `sBTC`/`zsBTC` (u2/u3), which correctly use `PYTH-BTC`, confirming the STX substitution for `stSTXbtc`/`zstSTXbtc` is inconsistent with the rest of the function and the asset's documented economics.

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L1-6)
```text
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; protocol-data - read-only utility contract
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; Provides optimized read-only functions to query protocol state
;; Batches related data to minimize cross-contract call overhead

```

**File:** mainnet/contracts/utility/v0-1-data.clar (L543-544)
```text
  ;; sBTC - Pyth oracle (BTC price)
  (if (is-eq aid sBTC) (default-to u0 (get-pyth-price PYTH-BTC))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L550-551)
```text
  ;; USDC - Pyth oracle
  (if (is-eq aid USDC) (default-to u0 (get-pyth-price PYTH-USDC))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L581-587)
```text
  ;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
  (if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
  ;; zstSTXbtc - stSTXbtc price x liquidity index
  (if (is-eq aid zstSTXbtc)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index stSTXbtc)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L395-417)
```text
    (ok final-price)))

(define-private (price-multi-resolve
  (data (list 64 { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (aids (list 64 uint)))
  (let ((init { output: (list), valid: true, aids: aids, idx: u0 })
        (response (fold iter-price-multi data init)))
    (asserts! (get valid response) ERR-ORACLE-MULTI)
    (ok (get output response))))

(define-private (iter-price-multi
  (oracle-data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint })
  (acc { output: (list 64 uint), valid: bool, aids: (list 64 uint), idx: uint }))
  (let ((valid (get valid acc))
        (skip? (asserts! valid acc))
        (asset-ids (get aids acc))
        (idx (get idx acc))
        ;; resolve price - will use cache for ztokens
        (price (unwrap! (price-resolve oracle-data) (merge acc { valid: false })))
        (next (unwrap-panic (as-max-len? (append (get output acc) price) u64))))
    { output: next,
      valid: true,
      aids: asset-ids,
```
