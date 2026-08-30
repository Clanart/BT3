Based on the code I was able to examine, the claimed vulnerability does not hold up.

`user-safe-mask` at `mainnet/contracts/market/v0-4-market.clar:428` is a private helper that ANDs a user's collateral bits against the DAO-enabled bitmap while leaving the debt bits (shifted out separately via `DEBT-MASK`/`DEBT-OFFSET`) untouched. This is not incidental — the surrounding helpers make the intent explicit: `get-position` (line 466-468) computes health with only enabled collateral, while `get-liquidation-position` (line 473-475) is explicitly commented "liquidation specific (enabled collateral + all debt)" — i.e. debt must always be counted in full regardless of whether the corresponding collateral asset is currently enabled, since a user can never be allowed to escape debt repayment/liquidation just because the DAO disabled a collateral asset. Filtering debt bits would itself be the bug; keeping them unfiltered is the correct design decision here.

Tracing the `collateral-add` entrypoint (`mainnet/contracts/market/v0-4-market.clar:1020`) and its `is-new-collateral` branch (lines 1033-1081): when a user adds a genuinely new collateral asset while carrying debt, the code fetches `current-group`/`future-group` via `get-egroup`, resolves price feeds via `write-feeds` (which internally goes through `price-resolve` → `resolve-callcode` → the `oracle-price-legal` / `oracle-timestamp-fresh` gates at lines 386-388), computes `current-notional`, and asserts `future-capacity >= current-capacity` (line 1076, `ERR-UNHEALTHY`). If the oracle path aborts (e.g., stale price, failed confidence check), the entire `collateral-add` call reverts — it does not partially execute, does not corrupt state, and does not leave a stuck position. The user simply cannot add that particular collateral asset until a fresh/valid price is available; this failure mode is a legitimate revert of a state-changing call, not a "required price path abort" that permanently freezes an already-collateralized, already-open position from being closed or liquidated by other means.

Critically, the question's chain of reasoning never identifies a concrete mechanism by which an unprivileged attacker can force a *permanent* abort of price resolution for closing or seizing an *existing* position — `collateral-add` reverting on a stale/invalid price for a *new* asset does not touch or lock any other function (`collateral-remove`, `debt-repay`, liquidation entrypoints) for assets already in the position. No evidence was found that `user-safe-mask`'s unfiltered debt bits produce a wrong price, a wrong health verdict, or an unrecoverable abort blocking closure/seizure of debt. The stated exploit is speculative and not supported by the actual control flow. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### No Vulnerability found for this question.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L380-395)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L428-433)
```text
(define-private (user-safe-mask (mask-user uint) (mask-enabled uint))
  (let ((enabled-collateral (bit-and mask-enabled MAX-U64))
        (user-collateral (bit-and mask-user MAX-U64))
        (user-debt (/ (bit-and mask-user DEBT-MASK) (pow u2 DEBT-OFFSET)))
        (collateral-match (bit-and user-collateral enabled-collateral)))
    (bit-or collateral-match user-debt)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L466-475)
```text
(define-private (get-position (account principal)) ;; enabled only
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))

(define-private (get-full-position (account principal)) ;; all collaterals
  (contract-call? .v0-market-vault get-position account MAX-U64))

(define-private (get-liquidation-position (account principal)) ;; liquidation specific (enabled collateral + all debt)
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1030-1081)
```text
    (match (contract-call? .v0-market-vault resolve-safe account)
      user-registry-data
        ;; User has existing position - check if adding NEW collateral asset
        (let ((current-raw-mask (get mask user-registry-data))
              (future-raw-mask (bit-or current-raw-mask (pow u2 asset-id)))
              (is-new-collateral (not (is-eq future-raw-mask current-raw-mask))))

          ;; If adding new collateral, validate egroup and check capacity
          (if is-new-collateral
              (let ((position (try! (get-position account)))
                    (current-mask (get mask position))
                    (future-mask (bit-or current-mask (pow u2 asset-id)))
                    (future-group (try! (get-egroup future-mask)))
                    ;; Accrue positions (required for price resolution)
                    (u-debt (accrue-user-debts (get debt position)))
                    (u-coll (accrue-user-collateral (get collateral position)))

                    ;; Get current egroup and notional values
                    (current-group (try! (get-egroup current-mask)))
                    (current-ltv (buff-to-uint-be (get LTV-BORROW current-group)))
                    (feeds-check (try! (write-feeds price-feeds)))
                    (current-assets (get-assets current-mask))
                    (current-notional (get-notional-evaluation { position: position, assets: current-assets }))
                    (current-debt-usd (get debt current-notional)))

                ;; ONLY check capacity if user has debt
                (if (> current-debt-usd u0)
                    ;; Calculate future mask and validate egroup exists
                    (let ((current-coll-usd (get collateral current-notional))
                          (current-capacity (* current-coll-usd current-ltv))
                          ;; Prime cache for new zToken collateral underlying if not already cached
                          (cache-primed (if (is-ztoken asset-id)
                                            (let ((vault-id (if (is-eq asset-id zSTX) STX
                                                            (if (is-eq asset-id zsBTC) sBTC
                                                            (if (is-eq asset-id zstSTX) stSTX
                                                            (if (is-eq asset-id zUSDC) USDC
                                                            (if (is-eq asset-id zUSDH) USDH
                                                            (if (is-eq asset-id zstSTXbtc) stSTXbtc
                                                            u100))))))))
                                              (try! (accrue-and-cache vault-id)))
                                            { index: u0, lindex: u0 }))
                          (added-collateral-value (try! (get-asset-value asset amount false)))
                          (future-ltv (buff-to-uint-be (get LTV-BORROW future-group)))
                          (future-coll-usd (+ current-coll-usd added-collateral-value))
                          (future-capacity (* future-coll-usd future-ltv)))
                      ;; CRITICAL CHECK: Future capacity must not decrease
                      (asserts! (>= future-capacity current-capacity) ERR-UNHEALTHY))
                    ;; No debt - skip capacity check
                    true))
              
              ;; Not new collateral - skip all checks (safe to add more)
              true))
```
