### Title
Mispriced `stSTXbtc`/`zstSTXbtc` collateral valued with STX feed instead of BTC feed, corrupting LTV and liquidation math - ([File: mainnet/contracts/market/v0-4-market.clar], [File: mainnet/contracts/proposals/mainnet/v0-init.clar], [File: mainnet/contracts/utility/v0-1-data.clar])

### Summary
### Finding Description
The `stSTXbtc` asset (asset ID 10, comment: "liquid staked STX with BTC yield") and its zToken `zstSTXbtc` (asset ID 11) are registered in the asset/oracle configuration with `STX-FEED-ID` and no BTC-adjusting callcode, meaning the market prices this asset directly off the STX/USD Pyth feed rather than a BTC-denominated feed: [1](#0-0) 

This is not a one-off typo in a single call site — the same STX-feed choice for `stSTXbtc`/`zstSTXbtc` is repeated in the local-testing init proposal and is reflected in the `v0-1-data.clar` read-only price helper, which explicitly comments "BTC price" while calling `get-pyth-price PYTH-STX`: [2](#0-1) 

At the market pricing layer, `resolve-callcode` in `v0-4-market.clar` routes the `CALLCODE-ZSTSTXBTC` case straight into `resolve-ztoken`, applying only the vault liquidity-index scaling — with no BTC/STX ratio conversion analogous to what `CALLCODE-ZSTSTX` applies via `resolve-ststx` before the ztoken index math: [3](#0-2) 

Contrast this with the parallel, correctly-designed `stSTX`/`zstSTX` pair, where the underlying `CALLCODE-STSTX` explicitly multiplies the STX price by a stSTX/STX ratio before any ztoken index scaling is applied: [4](#0-3) 

Because `stSTXbtc` is described as a BTC-yield-bearing liquid-staking derivative but has no distinct oracle feed/ratio and instead reuses the STX price 1:1 (scaled only by the vault liquidity index for the zToken form), its USD valuation tracks the STX/USD price rather than its actual BTC-pegged/BTC-yield value. This is the same class of bug as the external report's core lesson — a code path that silently produces an incorrect state value (there: unminted credit lost; here: the wrong price attached to the wrong asset) without any assertion catching the mismatch, because nothing in `resolve-callcode`, `oracle-price-legal`, or `oracle-timestamp-fresh` checks that the feed's asset matches the asset being priced.

### Impact Explanation
`zstSTXbtc` is enabled as a collateral asset in the egroup system (egroups 14–16 in the init proposal reference `zstSTXbtc` masks), so its USD valuation feeds directly into `current-ltv`, `is-healthy`/`is-healthy-with-mask`, and liquidation threshold checks in `v0-4-market.clar`. If STX and BTC prices diverge (which they will, being uncorrelated assets), the protocol will:
- Under-value or over-value `zstSTXbtc` collateral relative to its true BTC-denominated worth, allowing users to borrow more than their real collateral supports when STX/USD price is inflated relative to BTC/USD, directly enabling **theft of protocol funds through under-collateralized borrowing** (Critical: theft of funds at rest), or
- Trigger unwarranted or delayed liquidations when the divergence goes the other way, causing **freezing/loss of value for stSTXbtc collateral depositors** (High/Critical depending on direction).

Since this passes silently through `oracle-price-legal` (only checks `p > 0`) and `oracle-timestamp-fresh` (only checks staleness/monotonicity of the STX feed, which is "fresh" by construction), no on-chain guard rejects the mispriced value — health checks and liquidations will use a wrong-but-passing price indefinitely.

### Likelihood Explanation
This is a configuration/registration-time choice reflected consistently in the mainnet init proposal, not an attacker-triggered edge case — it fires on every single price resolution and every health/liquidation check involving `stSTXbtc` or `zstSTXbtc` collateral, for as long as the asset is enabled and STX/BTC prices are not pegged 1:1. Given `stSTXbtc` is enabled for collateral with real caps (`CAP-STSTXBTC-SUPPLY = 50M`), this is very likely to matter in practice.

However, this finding rests on the assumption — supported by the inline code comment "BTC price (liquid staked STX with BTC yield)" and the asset naming (`stSTXbtc`, `STSTXBTC-TOKEN`) — that this asset's true value should track BTC, not STX. It is possible the protocol intends `stSTXbtc` to actually be pegged/redeemable 1:1 in STX terms (e.g., a wrapped/rehypothecated version of stSTX with BTC yield accounted elsewhere), in which case using the STX feed would be correct by design and this would not be a genuine mispricing bug. This distinction could not be conclusively resolved from the indexed contract/doc excerpts available; the token contract `local-testing/contracts/utility/token/ststxbtc.clar` and `docs/vaults.md` (both partially indexed) may clarify the intended peg and yield mechanism.

### Recommendation
Confirm the true denomination/peg of `stSTXbtc`: if it is meant to be valued in BTC terms, register it with a BTC Pyth feed (as done for `sBTC`/`zsBTC`) or add a dedicated ratio-conversion callcode (analogous to `CALLCODE-STSTX`) that converts the BTC feed price using the actual stSTXbtc/BTC exchange ratio before the ztoken liquidity-index scaling in `resolve-callcode`. If it is genuinely STX-denominated, correct the misleading "BTC price" comments in `v0-1-data.clar`/`protocol-data.clar` and the `stSTXbtc` asset name/documentation to avoid future confusion, and add an explicit sanity check tying each oracle feed identifier to its expected asset class.

### Proof of Concept
Not independently executable from the indexed excerpts (no access to a full BTC price feed or the `ststxbtc.clar` redemption logic to confirm the peg). Conceptually: with STX/USD = $2 and BTC/USD = $100,000, a user deposits `zstSTXbtc` collateral. If `zstSTXbtc` is actually meant to represent BTC-denominated value, `resolve-callcode`/`resolve-ztoken` (path `v0-4-market.clar:349-358`) would price it at (STX price × liquidity index), i.e., roughly 1/50,000th of its true USD value in this example, letting the position appear far under-collateralized and get wrongly liquidated — or, if the STX price is inflated relative to BTC, letting the user borrow against phantom over-valued collateral. Confirming actual dollar magnitudes requires executing `price-resolve`/`resolve-callcode` against the live/mainnet feed data, which is outside the scope of the indexed code available here.

### Citations

**File:** mainnet/contracts/proposals/mainnet/v0-init.clar (L131-137)
```text
    ;; Asset ID 10: stSTXbtc
    (try! (contract-call? .v0-assets insert STSTXBTC-TOKEN
      { type: TYPE-PYTH, ident: STX-FEED-ID, callcode: none, max-staleness: MAX-STALENESS }))

    ;; Asset ID 11: zstSTXbtc (vault-ststxbtc)
    (try! (contract-call? .v0-assets insert .v0-vault-ststxbtc
      { type: TYPE-PYTH, ident: STX-FEED-ID, callcode: (some CALLCODE-ZSTSTXBTC), max-staleness: MAX-STALENESS }))
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
