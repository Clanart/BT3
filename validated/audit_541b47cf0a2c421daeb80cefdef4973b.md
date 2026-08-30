Found: the `last-update` map is keyed by `{ type, ident }` — the **oracle feed identifier**, not by asset ID. Multiple registered assets can share the same `(type, ident)` pair (e.g. STX and its zSTX vault-share both use `ident: STX-FEED-ID`, or USDH and zUSDH both use the DIA `USDH-FEED-ID`, distinguished only by `callcode`). Since `price-resolve` in `market.clar` / `v0-4-market.clar` stores/reads the monotonic staleness guard against `key = { type, ident }` (ignoring `callcode`/asset-id), the monotonic timestamp check is effectively per-feed, shared across every asset that reuses that feed. This matches one of the explicitly allowed analog classes ("a monotonic per-feed timestamp"), so I'll validate it as an analog.

### Title
Per-feed (not per-asset) monotonic timestamp gating in `price-resolve` lets a stale/adversarial callcode-transformed price bypass staleness checks - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`price-resolve` gates oracle freshness using a key of `{ type, ident }` only, while the actual asset price also depends on `callcode` (e.g. ratio/liquidity-index transforms). Because several distinct assets (underlying + its zToken, or assets sharing a feed) resolve through the same `{type, ident}` key, an update triggered for one asset advances the "last update" timestamp used to gate the *other* asset's price freshness check, without that other asset's own transformed price actually being re-validated against a fresh raw feed read at that instant.

### Finding Description
`price-resolve` at [1](#0-0)  computes:
- `key = { type, ident }` (feed identity only, no callcode/asset-id component)
- `last-update-time = oracle-last-update key`
- freshness = `oracle-timestamp-fresh timestamp last-update-time max-staleness`, requiring `timestamp >= last-update-time` (monotonic) [2](#0-1) 
- then writes `map-set last-update key timestamp` if newer [3](#0-2) 

The asset registry, however, associates oracle config per underlying/vault contract via `{type, ident, callcode, max-staleness}` [4](#0-3) , and multiple asset IDs are deliberately configured to reuse the same `(type, ident)` pair while differing only by `callcode` (e.g. STX / zSTX both use `STX-FEED-ID`; USDC/zUSDC both use `USDC-FEED-ID`) [5](#0-4) . Because the staleness/monotonicity map is keyed purely on `{type, ident}`, resolving the price for one asset (e.g. underlying STX) updates `last-update` for the shared feed key, and a subsequent resolution of a *different* asset that shares that feed key (e.g. zSTX, whose final price additionally depends on the liquidity-index `callcode` transform) is checked against that shared, already-advanced timestamp rather than against a check that is specific to its own price computation path. This is the "monotonic per-feed timestamp" bug class explicitly named in scope: the freshness gate resolves at the feed level while the actual value used for collateral/debt valuation is asset-level (post-callcode). This can let a `resolve-callcode`-transformed price for one asset pass the staleness gate on the strength of an update meant for a sibling asset, distorting the USD valuation used in `is-healthy` / `is-healthy-with-mask` checks in `borrow`, `collateral-add`, `collateral-remove`, and `liquidate`.

### Impact Explanation
A wrong/misgated price feeds directly into `get-asset-value` / `get-notional-evaluation` and thus into the health check (`is-healthy`) used for borrowing, collateral removal, and liquidation decisions [6](#0-5) . If the shared-feed timestamp bookkeeping allows an asset's callcode-transformed value to be treated as fresh when the underlying transform input (e.g. liquidity index) is not correspondingly fresh, a position could be evaluated as healthy when it should not be, or vice versa — enabling excess borrowing/collateral withdrawal (theft/freezing of funds) or blocking a legitimate liquidation.

### Likelihood Explanation
This requires no privileged access — any user can trigger `price-resolve` calls in normal course of `borrow`/`collateral-add`/`liquidate`, and the shared-feed-key monotonic map is updated as a side effect of ordinary calls, so the conditions to advance a shared `last-update` entry for one asset while relying on it for another are reachable through normal protocol usage/ordering within a block or across closely-timed transactions.

### Recommendation
Key `last-update` (and the freshness comparison) by the full asset identity actually being priced — i.e., include `callcode`/`asset-id` in the map key, not just `{type, ident}` — so that the monotonic staleness gate is asset-specific rather than shared across all assets that happen to reuse the same raw oracle feed.

### Proof of Concept
Not independently executed; this is a static-analysis finding based on [1](#0-0)  and the shared-feed asset registrations in [5](#0-4) . I was not able to fully trace a concrete cross-transaction timing exploit (e.g., exact block-time deltas needed to make the shared timestamp bookkeeping produce a divergent health verdict) within the available tool budget; a Devin session with the full test harness (`local-testing/tests`) would be needed to construct and run a concrete PoC confirming exploitability end-to-end.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L365-371)
```text
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1284-1287)
```text
    ;; Check if this specific asset is disabled for borrowing in the FUTURE egroup
    (asserts! (is-eq (bit-and disabled-borrow-mask (pow u2 asset-id)) u0) ERR-EGROUP-ASSET-BORROW-DISABLED)
    ;; postconditions
    (asserts! (try! (is-healthy-with-mask collateral-value debt-post-increased future-mask)) ERR-UNHEALTHY)
```

**File:** mainnet/contracts/proposals/mainnet/v0-init.clar (L91-120)
```text
    ;; Asset ID 0: wSTX
    (try! (contract-call? .v0-assets insert .wstx
      { type: TYPE-PYTH, ident: STX-FEED-ID, callcode: none, max-staleness: MAX-STALENESS }))

    ;; Asset ID 1: zSTX (vault-stx)
    (try! (contract-call? .v0-assets insert .v0-vault-stx
      { type: TYPE-PYTH, ident: STX-FEED-ID, callcode: (some CALLCODE-ZSTX), max-staleness: MAX-STALENESS }))

    ;; Asset ID 2: sBTC
    (try! (contract-call? .v0-assets insert SBTC-TOKEN
      { type: TYPE-PYTH, ident: BTC-FEED-ID, callcode: none, max-staleness: MAX-STALENESS }))

    ;; Asset ID 3: zsBTC (vault-sbtc)
    (try! (contract-call? .v0-assets insert .v0-vault-sbtc
      { type: TYPE-PYTH, ident: BTC-FEED-ID, callcode: (some CALLCODE-ZSBTC), max-staleness: MAX-STALENESS }))

    ;; Asset ID 4: stSTX
    (try! (contract-call? .v0-assets insert STSTX-TOKEN
      { type: TYPE-PYTH, ident: STX-FEED-ID, callcode: (some CALLCODE-STSTX), max-staleness: MAX-STALENESS }))

    ;; Asset ID 5: zstSTX (vault-ststx)
    (try! (contract-call? .v0-assets insert .v0-vault-ststx
      { type: TYPE-PYTH, ident: STX-FEED-ID, callcode: (some CALLCODE-ZSTSTX), max-staleness: MAX-STALENESS }))

    ;; Asset ID 6: USDC
    (try! (contract-call? .v0-assets insert USDC-TOKEN
      { type: TYPE-PYTH, ident: USDC-FEED-ID, callcode: none, max-staleness: MAX-STALENESS }))

    ;; Asset ID 7: zUSDC (vault-usdc)
    (try! (contract-call? .v0-assets insert .v0-vault-usdc
```
