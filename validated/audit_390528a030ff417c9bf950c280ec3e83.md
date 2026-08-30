## Title
Hardcoded Pyth storage contract address in `call-pyth` cannot follow Pyth's own governance-controlled storage migration - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`v0-4-market.clar`'s price-read path calls the Pyth storage contract by a **hardcoded absolute principal** instead of going through the governance-controlled execution plan that Pyth itself uses to route both writes and reads. This is the same bug class as the reported `migrateRewardPool` issue: a "migration" capability exists at the protocol level (Pyth's `pyth-governance-v3.update-pyth-storage-contract`), but a downstream consumer (`market.clar`) holds an immutable reference to the old target and cannot follow the migration, so it silently freezes.

### Finding Description
Pyth's own bridge design anticipates rotating its storage/decoder/wormhole contracts over time via a governance-controlled `execution-plan` (see `pyth-governance-v3.clar`'s `update-pyth-storage-contract`, and the historical deployment plans that already show storage contracts being rotated `pyth-storage-v1` → `pyth-storage-v4`) [1](#0-0) . Both the read path (`pyth-oracle-v4.get-price`/`read-price-feed`) and the write path (`verify-and-update-price-feeds`) validate the caller-supplied execution plan against the governance-tracked current plan before touching storage [2](#0-1) .

`v0-4-market.clar`, however, bypasses this validated indirection for price reads and calls the Pyth storage contract directly by hardcoded principal: [3](#0-2) 

Meanwhile the write path in the same file (`write-feed`) also hardcodes the exact same trio of addresses (`pyth-storage-v4`, `pyth-pnau-decoder-v3`, `wormhole-core-v4`) when invoking `verify-and-update-price-feeds`: [4](#0-3) 

If Pyth's governance rotates the active storage contract (exactly as it has already done historically from v1 to v4, per the deployment plans), `market.clar`'s write path would start failing `check-execution-flow` validation inside `pyth-oracle-v4`/`pyth-governance-v3` because it still submits the deprecated execution plan, so `pyth-storage-v4` stops receiving fresh price writes routed via Zest's oracle updater. `call-pyth` (the read path) keeps reading directly from that now-abandoned `pyth-storage-v4` contract, whose `publish-time` stops advancing. Zest's own staleness gate then reads that address forever:

`oracle-timestamp-fresh` compares the fetched `timestamp` against `stacks-block-time` and against the last-seen monotonic timestamp for that feed: [5](#0-4) 

Because `market.clar` has no mechanism analogous to `pyth-governance-v3`'s `update-pyth-storage-contract` to repoint `call-pyth`/`write-feed` at the new storage contract — the addresses are baked directly into the Clarity source — every subsequent price resolution for Pyth-priced assets (STX, sBTC, USDC, etc.) will fail the `max-staleness` check and revert with `ERR-ORACLE-INVARIANT`, exactly mirroring how `CurveConvex2Token`'s immutable reward pool reference in the original report cannot follow `AbstractRewardManager.migrateRewardPool`.

### Impact Explanation
Once the upstream Pyth storage contract Zest hardcodes is deprecated/rotated by Pyth governance, all functions requiring a fresh oracle price for Pyth-quoted assets — `borrow`, `withdraw`, `liquidate`, health checks — begin reverting via `ERR-ORACLE-INVARIANT` in `price-resolve`. This causes a protocol-wide freeze of any collateral/debt action gated on Pyth pricing: users cannot withdraw supplied collateral, cannot be liquidated (or cannot liquidate undercollateralized positions), and new borrows are blocked. This matches the in-scope "temporary freezing of funds" impact class, and if liquidations are blocked for a sustained period while underlying collateral value drops, unresolved undercollateralized positions can drive the protocol toward insolvency.

### Likelihood Explanation
This is not hypothetical - the repository's own Pyth deployment history documents a real storage-contract rotation (`pyth-storage-v1` → `pyth-storage-v4`) [6](#0-5) , meaning the trigger condition (Pyth governance updating the active storage contract again) is a normal, expected operational event for that bridge, not a rare edge case.

### Recommendation
Route `call-pyth` and `write-feed` through the governance-tracked execution plan (e.g., call `pyth-governance-v3.get-current-execution-plan` or accept the plan as a parameter validated against it) instead of hardcoding `pyth-storage-v4`/`pyth-pnau-decoder-v3`/`wormhole-core-v4` principals in `v0-4-market.clar`, so that Zest's oracle read/write path automatically tracks any future Pyth storage-contract migration without requiring a full market contract redeployment.

### Proof of Concept
1. Pyth governance calls `update-pyth-storage-contract` on `pyth-governance-v3` to rotate the active storage contract from `pyth-storage-v4` to a new `pyth-storage-v5` [1](#0-0) .
2. Zest's `write-feed` in `v0-4-market.clar` keeps submitting the old execution plan (`pyth-storage-v4`, etc.); `check-execution-flow`/`check-storage-contract` in `pyth-governance-v3`/`pyth-oracle-v4` rejects it, so no new prices are written into `pyth-storage-v4` going forward.
3. `call-pyth` in `v0-4-market.clar` still reads directly from `pyth-storage-v4`, whose `publish-time` is now frozen at the last update before migration.
4. `price-resolve`'s `oracle-timestamp-fresh` check computes `delta = stacks-block-time - ts` that grows unboundedly and exceeds `max-staleness`, causing `ERR-ORACLE-INVARIANT` on every subsequent call that needs a Pyth price for that asset, freezing borrow/withdraw/liquidate operations for that asset.

### Citations

**File:** local-testing/contracts/pyth/contracts/pyth-governance-v3.clar (L238-253)
```text
(define-public (update-pyth-storage-contract (vaa-bytes (buff 8192)) (wormhole-core-contract <wormhole-core-trait>))
	(let ((expected-execution-plan (var-get current-execution-plan))
			(vaa (try! (contract-call? wormhole-core-contract parse-and-verify-vaa vaa-bytes)))
			(ptgm (try! (parse-and-verify-ptgm (get payload vaa) (get sequence vaa)))))
		;; Ensure action's expected
		(asserts! (is-eq (get action ptgm) PTGM_UPDATE_PYTH_STORAGE_ADDRESS) ERR_UNEXPECTED_ACTION)
		;; Ensure that the action is authorized
		(try! (check-update-source (get emitter-chain vaa) (get emitter-address vaa)))
		;; Ensure that the latest wormhole contract is used
		(try! (expect-active-wormhole-contract wormhole-core-contract expected-execution-plan))
		;; Update execution plan
		(let ((updated-data (try! (parse-principal (get body ptgm)))))
			(var-set current-execution-plan (merge expected-execution-plan { pyth-storage-contract: updated-data }))
			;; Emit event
			(print { type: "pyth-storage-contract", action: "updated", data: updated-data })
			(ok updated-data))))
```

**File:** local-testing/contracts/pyth/contracts/pyth-oracle-v4.clar (L13-20)
```text
(define-public (get-price
		(price-feed-id (buff 32))
		(pyth-storage-address <pyth-storage-trait>))
	(begin
		;; Check execution flow
		(try! (contract-call? .pyth-governance-v3 check-storage-contract pyth-storage-address))
		;; Perform contract-call
		(contract-call? pyth-storage-address read-price-with-staleness-check price-feed-id)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L128-144)
```text
;; Write a single Pyth price feed update using fold accumulator pattern
(define-private (write-feed (feed (buff 8192)) (status (response bool uint)))
  (match status
    success-status
      (match (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-oracle-v4 verify-and-update-price-feeds
          feed
          {
            pyth-storage-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4,
            pyth-decoder-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-pnau-decoder-v3,
            wormhole-core-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.wormhole-core-v4,
          }
        )
        update-success (ok true)
        update-failed ERR-PRICE-FEED-UPDATE-FAILED)
    error-status status
  )
)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L308-310)
```text
(define-private (call-pyth (ident (buff 32)))
  (let ((res (unwrap! (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4 get-price ident) ERR-ORACLE-PYTH)))
    (ok res)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L365-395)
```text
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))

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

**File:** local-testing/contracts/pyth/deployments/v1/2-upgrade-pyth-oracle-v2.mainnet-plan.yaml (L26-40)
```yaml
    - id: 1
      transactions:
        - contract-call:
            contract-id: SP2T5JKWWP3FYYX4YRK8GK5BG2YCNGEAEY2P2PKN0.pyth-oracle-v3
            expected-sender: SP2T5JKWWP3FYYX4YRK8GK5BG2YCNGEAEY2P2PKN0
            method: verify-and-update-price-feeds
            parameters:
              # PNAU payload
              - 0x504e41550100000003b801000000030d026b102bc02cdb8bdb4894627bf84a2e0fdb60262e086adaef120602351c27a68709e7d353b094f52b8d811845a717accc2635d71ccd67ba41c37d5dad886deb7800030bbe7dda773f536db496a3573afceb9cd952ddce136edce96bc2bc52cfcfde494819a6765545afaea3ca62a0098ee18c45bf2b71afad5e26bbb3d358cac66299000443bfed7d6d49ffe8980d955e1704c37dd9cea9b5c477c81cc0713ed2c0f30e913054ccb53fd81d492e5226bf8b2f593debe5b6a43b9991b176949b5db204ccde00061509d1b53e47f1542397f79b14ffef49843279957e524e55cc3d7597c326713e742ba3ad33fc9ff484e0421d19cde9f4be90042116b3ea8962c592f8bbda23e40107c29f886d3fe660f214c32d00769c49156fe7f1683c881b675f81c0a00b50d3f37421b2193c7f2aa95ee6ca02ddc6c79877f6685e5696ed49542a71340ef8beca01085743d5682e17c149ffa13f03220204d4a797a9064708f7c9ce3fc5e5daad9a5e3620def0b69893a80d7079e3 ... (truncated)
              - "{
                pyth-storage-contract: 'SP2T5JKWWP3FYYX4YRK8GK5BG2YCNGEAEY2P2PKN0.pyth-storage-v1,
                pyth-decoder-contract: 'SP2T5JKWWP3FYYX4YRK8GK5BG2YCNGEAEY2P2PKN0.pyth-pnau-decoder-v2,
                wormhole-core-contract: 'SP2T5JKWWP3FYYX4YRK8GK5BG2YCNGEAEY2P2PKN0.wormhole-core-v3
                }"
            cost: 1000000
```
