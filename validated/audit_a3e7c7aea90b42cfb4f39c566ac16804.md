### Title
stSTXbtc/zstSTXbtc valued off the wrong Pyth feed (STX instead of BTC) in the read-only account/asset pricing helper - ([File: mainnet/contracts/utility/v0-1-data.clar])

### Summary
`get-asset-price` in `v0-1-data.clar` prices `stSTXbtc` and `zstSTXbtc` using `PYTH-STX` while the surrounding comments and variable names (`btc-price`) explicitly say it should be `PYTH-BTC`, causing this read-only helper to compute the wrong USD value for those assets.

### Finding Description
`get-asset-price` maps each asset id to a Pyth/DIA feed to compute its USD value: [1](#0-0) 
```
;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
(if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
;; zstSTXbtc - stSTXbtc price x liquidity index
(if (is-eq aid zstSTXbtc)
    (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
          (lindex (get-vault-liquidity-index stSTXbtc)))
      (mul-div-down btc-price lindex INDEX-PRECISION))
```
Both branches are explicitly documented and named as BTC-price lookups, yet they call `get-pyth-price PYTH-STX` instead of `PYTH-BTC`. Every other asset in this function correctly matches its comment/variable name to the feed constant it invokes (e.g. `sBTC` uses `PYTH-BTC`, `zsBTC` uses `PYTH-BTC`) — `stSTXbtc`/`zstSTXbtc` are the only two branches where the feed constant does not match the intended asset. This is a price-attached-to-wrong-asset defect: the STX price (a different, generally much lower-value asset in this protocol context) is substituted for the BTC price used to value stSTXbtc-denominated positions in this helper's collateral/debt sums (`sum-collateral-usd`, `sum-debt-usd`).

However, I could not confirm within the available context that this hardcoded mapping in `v0-1-data.clar` is consumed by the actual on-chain health/liquidation enforcement path. The core market contract (`v0-4-market.clar`) resolves prices via `price-resolve`/`resolve-price-feed`/`resolve-callcode`, which reads the oracle `ident` configured per-asset in the `assets` registry rather than this hardcoded STX/BTC constant table. `v0-1-data.clar` appears to be a separate read-only "protocol data" utility contract (exposing `get-all-assets`, `get-all-reserves`, `get-reserve`, etc.) used for querying/dashboards rather than for gating borrows, withdrawals, or liquidations on-chain. I was not able to verify, with the tools and context available, whether any public/privileged function reads `sum-collateral-usd`/`sum-debt-usd`/`get-asset-price` from this contract to make an actual solvency or liquidation decision that moves funds.

### Impact Explanation
If this pricing helper is purely informational (dashboard/off-chain query support) and not used to gate any fund-moving on-chain decision, the bug has no protocol-fund impact and would not meet the Critical/High/temporary-freezing bar required by the rules. If, contrary to what I could verify, some public entry point uses this contract's `get-asset-price`/`sum-collateral-usd`/`sum-debt-usd` to authorize borrows, withdrawals, or liquidations, then substituting the STX feed for the BTC feed for `stSTXbtc`/`zstSTXbtc` would produce grossly wrong collateral/debt USD valuations, potentially enabling under-collateralized borrowing (temporary/permanent freezing of protocol funds) or blocking legitimate liquidations. I cannot confirm this call path exists in the repository content I was able to inspect.

### Likelihood Explanation
The defect is present in code as written (a `grep`/read of the file confirms the STX constant is used in both BTC-labeled branches), so it triggers deterministically any time `get-asset-price` is invoked for `stSTXbtc` or `zstSTXbtc` — no attacker action is required to trigger the miscalculation itself. The open question is solely whether this contract's output feeds into an economically consequential decision.

### Recommendation
Replace `PYTH-STX` with `PYTH-BTC` in both the `stSTXbtc` and `zstSTXbtc` branches of `get-asset-price` in `mainnet/contracts/utility/v0-1-data.clar` to match the documented intent, and audit every consumer of this function/contract to confirm whether it participates in any fund-moving decision; if it does, treat this as a priority fix and add a test asserting `stSTXbtc`/`zstSTXbtc` USD values track the BTC feed.

### Proof of Concept
1. Register/observe an account holding `stSTXbtc` or `zstSTXbtc` as collateral or debt.
2. Call the read-only functions in `v0-1-data.clar` that route through `get-asset-price` (e.g. `sum-collateral-usd`/`sum-debt-usd` consumers or any exposed read-only wrapper) for that asset.
3. Compare the returned USD value against the BTC market price vs. the STX market price — the function currently returns a value derived from the STX Pyth feed (`PYTH-STX`) rather than BTC (`PYTH-BTC`), which is verifiable directly from the source at [1](#0-0) .

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L581-588)
```text
  ;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
  (if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
  ;; zstSTXbtc - stSTXbtc price x liquidity index
  (if (is-eq aid zstSTXbtc)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index stSTXbtc)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
  ;; Unknown asset - return 0
```
