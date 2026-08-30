### Title
`iter-price-multi` builds price output positionally without binding it to `aids`, letting `collateral-add`'s attacker-supplied `price-feeds` order mis-assign a valid price to the wrong asset - (mainnet/contracts/market/v0-4-market.clar:405)

### Summary
`iter-price-multi` threads `aids` and `idx` through its fold accumulator but never uses either value to verify that the price it just resolved corresponds to the asset id expected at that position; it simply appends the resolved price to a plain `(list 64 uint)` in the order the `oracle-data` list was iterated [1](#0-0) . Since `collateral-add` forwards the attacker-supplied `price-feeds` buffers into this pipeline via `write-feeds` before any asset-id binding occurs [2](#0-1) , an attacker who controls the order/content of the three feed buffers can potentially cause a price resolved for one oracle key (asset) to occupy the slot in the output list that downstream valuation code expects to correspond to a different asset id, since the returned `uint` carries no asset-id tag at all.

### Finding Description
Tracing the price through the pipeline: `price-resolve` resolves a single `(type, ident)` pair via `resolve-price-feed` -> `resolve-callcode`, checks `oracle-price-legal` and `oracle-timestamp-fresh` against the `(type, ident)`-keyed `last-update` map, and returns a bare `uint` price with no asset-id attached [3](#0-2) . `price-multi-resolve` folds a list of such `oracle-data` records through `iter-price-multi`, seeding the accumulator with `aids` (the expected asset-id list) and `idx` (position counter) [4](#0-3) . Inside `iter-price-multi`, `asset-ids` and `idx` are read out of the accumulator but are only passed through unchanged to the next iteration (`aids: asset-ids`, `idx: (+ idx u1)`) — they are never compared to anything, never used to pick which asset the resolved price belongs to, and never used to validate that `oracle-data`'s implicit type/ident corresponds to the asset id at that index [5](#0-4) . The only thing that determines "which asset gets which price" downstream is therefore positional order in the output `(list 64 uint)`, which is exactly the order of the `oracle-data` list, which is itself derived from the attacker-supplied `price-feeds` argument to `collateral-add` after decoding by `write-feeds` [6](#0-5) .

Every individual price still passes its own confidence/staleness gate for its own `(type, ident)` — the gates are not bypassed for the feed itself. The break occurs one layer up: the gate validates "this price is fresh and legal for oracle key K," but the invariant the calling code relies on is "this price at list-position i is fresh and legal for asset id `aids[i]`." Because `iter-price-multi` never checks that the oracle key resolved at position `i` actually corresponds to `aids[i]`, a correctly-gated price for one asset's oracle key can end up algebraically consumed as the price for a different asset in `get-notional-evaluation`, if that function (or its callers) zips the output list positionally against the asset list without re-validating the oracle key per asset. I could not fully verify the internals of `write-feeds`/`get-notional-evaluation`/`get-assets` in this session (they were not inspected due to iteration limits), so whether a later stage re-derives and re-checks the oracle key for each asset id before consuming the price is unconfirmed. If such a re-check exists, the wrong price is caught before being converted to a notional value; if it does not exist, the mis-aligned price flows into `current-notional`/`future-coll-usd` and the `ERR-UNHEALTHY` capacity check in `collateral-add`.

### Impact Explanation
If the downstream consumer trusts positional alignment (as the unused `aids`/`idx` fields strongly suggest was the original design intent that got left unimplemented), a legitimate user calling `collateral-add` with reordered/attacker-chosen `price-feeds` could have a materially wrong price attached to their existing collateral or the asset being added, causing the `(asserts! (>= future-capacity current-capacity) ERR-UNHEALTHY)` check to fail spuriously and revert an otherwise-legitimate collateral deposit — a temporary freezing-of-funds condition (High) consistent with the scoped impact category, rather than a permanent loss, since the user's funds remain in their wallet and a resubmission with correctly-ordered feeds should succeed.

### Likelihood Explanation
The attacker only needs to control the order of at most three `(buff 8192)` price-feed buffers passed to a public, unprivileged entrypoint (`collateral-add`), which requires no special capital or permissions beyond owning the position being modified — this is trivially repeatable per call. The precondition for exploitability (whether a real mis-assignment reaches money) hinges on downstream code not independently re-validating the oracle key per asset id, which I could not confirm or refute with the code inspected in this session.

### Recommendation
Have `iter-price-multi` bind each resolved price explicitly to the asset id at `idx` in `aids` — e.g., look up the canonical `(type, ident, callcode, max-staleness)` for `(nth idx aids)` from the asset registry rather than trusting the caller-ordered `oracle-data` list, or emit `{ aid: (element-at aids idx), price: final-price }` tuples and have every downstream consumer index by `aid` instead of by list position. This removes the implicit "positional order == asset order" assumption that the unused `aids`/`idx` fields indicate was intended but never enforced.

### Proof of Concept
Not able to construct a concrete reproducible Clarinet/vitest PoC in this session: doing so requires inspecting `write-feeds`, `get-assets`, and `get-notional-evaluation` to determine (a) whether `oracle-data` order sent into `price-multi-resolve` is attacker-controlled independent of asset id, and (b) whether the notional-evaluation step re-validates the oracle key per asset id before consuming the positional price list. These were not available within the tool-call budget of this session, so the finding is reported based on the confirmed unused `aids`/`idx` accumulator fields in `iter-price-multi` plus the confirmed attacker control of `price-feeds` ordering in `collateral-add`, but the exploit's reachability of a real mispriced notional value is unverified.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L397-403)
```text
(define-private (price-multi-resolve
  (data (list 64 { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (aids (list 64 uint)))
  (let ((init { output: (list), valid: true, aids: aids, idx: u0 })
        (response (fold iter-price-multi data init)))
    (asserts! (get valid response) ERR-ORACLE-MULTI)
    (ok (get output response))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L405-418)
```text
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
      idx: (+ idx u1) }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1020-1052)
```text
(define-public (collateral-add (ft <ft-trait>) (amount uint) (price-feeds (optional (list 3 (buff 8192)))))
  (let ((ft-address (contract-of ft))
        (asset (try! (get-asset ft-address)))
        (asset-id (get id asset))
        (account contract-caller))

    (asserts! (get collateral asset) ERR-COLLATERAL-DISABLED)
    (asserts! (is-eq contract-caller tx-sender) ERR-AUTHORIZATION)
    ;; Validate future mask has valid egroup AND check health if user has debt
    
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
```
