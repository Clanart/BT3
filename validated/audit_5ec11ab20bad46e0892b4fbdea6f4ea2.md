Based on my research, I found a strong structural analog to the reported bug in the market's Pyth price integration.### Title
Market hardcodes Pyth infrastructure contract principals instead of resolving them from `pyth-governance-v3`'s execution plan, freezing all price updates and price-dependent operations after a legitimate Pyth infrastructure upgrade - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
The reported bug class is "a privileged component is legitimately migrated to a new address, but a dependent contract keeps wired state pointing to the old address, breaking a downstream operation." `v0-4-market.clar` reproduces this pattern for its Pyth oracle wiring: `write-feed` and `call-pyth` hardcode specific Pyth v4 contract principals rather than reading the live execution plan tracked by `pyth-governance-v3.clar`. When Pyth's own governance rotates any of its infrastructure contracts, market's price-push path silently breaks, and once the last successfully-recorded price ages past `max-staleness`, the protocol's staleness gate blocks all price-dependent user operations, including liquidations.

### Finding Description
`write-feed`, used to push fresh Pyth price updates before health/liquidation calculations, calls a fixed principal set: [1](#0-0) 

Likewise, `call-pyth` reads prices from a fixed storage contract principal rather than the live one: [2](#0-1) 

These addresses are not derived from `pyth-governance-v3`'s `current-execution-plan`, which is exactly the mechanism Pyth itself provides for rotating its storage/decoder/oracle/wormhole-core contracts: [3](#0-2) 

The Pyth oracle contract enforces that callers use the *currently active* plan — calling `verify-and-update-price-feeds` with an outdated storage/decoder/wormhole-core combination is rejected once the plan has moved on, as shown by the governance test suite (`toBeErr(Cl.uint(4003))` after an execution-plan update): [4](#0-3) 

Since market.clar's `write-feed` always submits the *original* fixed plan (storage-v4/decoder-v3/wormhole-core-v4/oracle-v4), a legitimate Pyth-side migration to newer infrastructure (exactly analogous to the reported `setLiquidationManager()` migrating to a new privileged address without updating dependent wiring) makes every subsequent `write-feed` call fail with `ERR-PRICE-FEED-UPDATE-FAILED`, or simply be silently rejected by the outdated pyth-oracle-v4 contract. `call-pyth` continues reading from the old `pyth-storage-v4`, whose values stop advancing because no one can write fresh data to it anymore through this path.

The resulting frozen price feeds are gated by market's own staleness/monotonic check: [5](#0-4) 

Once `stacks-block-time - timestamp` exceeds `max-staleness` for the affected asset, `price-resolve` reverts with `ERR-ORACLE-INVARIANT` for *every* code path that needs that asset's price — deposits, borrows, repayments, and critically, liquidation health checks (`get-notional-evaluation`, `process-debt-asset`, `process-collateral-asset` all rely on `price-resolve`/`get-asset-value`).

### Impact Explanation
This lands on **temporary freezing of funds (High)**. Once the hardcoded Pyth plan drifts from the live plan and the cached price ages past `max-staleness`, all price-dependent operations for the affected asset revert, including liquidations of unhealthy positions. Borrowers with underwater positions cannot be liquidated, and healthy users cannot deposit/withdraw/borrow/repay against that asset until the DAO redeploys/repoints market.clar to the new Pyth plan. This mirrors the original report's core harm: liquidation (and other operations) failing because privileged-address rotation wasn't propagated to a dependent contract's stored references.

### Likelihood Explanation
Medium. It requires an external, legitimate event — Pyth governance rotating its storage/decoder/oracle/wormhole-core contract via `update-pyth-storage-contract`/`update-pyth-decoder-contract`/`update-pyth-oracle-contract`/`update-wormhole-core-contract` — which is a normal, expected maintenance action for the Pyth protocol over the life of the market (v4 contracts are already versioned, implying rotation is anticipated). No malicious actor or DAO misconfiguration is needed; it's a maintenance action on a third party's infra that Zest's contract fails to track dynamically. Likelihood is not "High" only because it depends on Pyth actually rotating those specific contracts while Zest hasn't yet redeployed a market update.

### Recommendation
Have `v0-4-market.clar`'s `write-feed`/`call-pyth` fetch the live execution plan from `pyth-governance-v3.get-current-execution-plan` (or an equivalent DAO-updatable data-var storing the four contract principals) at call time instead of hardcoding `pyth-oracle-v4`/`pyth-storage-v4`/`pyth-pnau-decoder-v3`/`wormhole-core-v4`. Alternatively, add a DAO-controlled setter for these four references (mirroring the fix recommended for `setLiquidationManager`) so operators can update market's wiring the moment Pyth rotates infrastructure, before any staleness-driven freeze occurs.

### Proof of Concept
1. Pyth governance calls `update-pyth-storage-contract` (or `update-pyth-decoder-contract` / `update-wormhole-core-contract` / `update-pyth-oracle-contract`) on `pyth-governance-v3`, moving `current-execution-plan` to new contract addresses.
2. Any caller subsequently invokes a market function that triggers `write-feed` (e.g. `liquidate` with `price-feeds` supplied); `write-feed` still submits the old fixed plan to `pyth-oracle-v4.verify-and-update-price-feeds`, which now fails the plan-match check (per `expect-active-storage-contract`/`expect-active-decoder-contract` logic, error `4003` as shown in the governance test suite) — `write-feed` returns `ERR-PRICE-FEED-UPDATE-FAILED`.
3. `call-pyth`/`resolve-pyth` continue reading the last price recorded in the old `pyth-storage-v4` before the migration; its `publish-time` stops advancing.
4. Time passes until `stacks-block-time - timestamp > max-staleness` for the affected asset.
5. Any subsequent call into `price-resolve` for that asset (borrow, repay, deposit, or liquidate) now reverts with `ERR-ORACLE-INVARIANT`, freezing all operations — including liquidation of already-unhealthy positions in that asset — until the DAO manually redeploys or patches market.clar to reference the new Pyth plan.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L129-144)
```text
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

**File:** local-testing/contracts/pyth/contracts/pyth-governance-v3.clar (L204-253)
```text
(define-public (update-pyth-oracle-contract (vaa-bytes (buff 8192)) (wormhole-core-contract <wormhole-core-trait>))
	(let ((expected-execution-plan (var-get current-execution-plan))
			(vaa (try! (contract-call? wormhole-core-contract parse-and-verify-vaa vaa-bytes)))
			(ptgm (try! (parse-and-verify-ptgm (get payload vaa) (get sequence vaa)))))
		;; Ensure action's expected
		(asserts! (is-eq (get action ptgm) PTGM_UPDATE_PYTH_ORACLE_ADDRESS) ERR_UNEXPECTED_ACTION)
		;; Ensure that the action is authorized
		(try! (check-update-source (get emitter-chain vaa) (get emitter-address vaa)))
		;; Ensure that the latest wormhole contract is used
		(try! (expect-active-wormhole-contract wormhole-core-contract expected-execution-plan))
		;; Update execution plan
		(let ((updated-data (try! (parse-principal (get body ptgm)))))
			(var-set current-execution-plan (merge expected-execution-plan { pyth-oracle-contract: updated-data }))
			;; Emit event
			(print { type: "pyth-oracle-contract", action: "updated", data: updated-data })
			(ok updated-data))))

(define-public (update-pyth-decoder-contract (vaa-bytes (buff 8192)) (wormhole-core-contract <wormhole-core-trait>))
	(let ((expected-execution-plan (var-get current-execution-plan))
			(vaa (try! (contract-call? wormhole-core-contract parse-and-verify-vaa vaa-bytes)))
			(ptgm (try! (parse-and-verify-ptgm (get payload vaa) (get sequence vaa)))))
		;; Ensure action's expected
		(asserts! (is-eq (get action ptgm) PTGM_UPDATE_PYTH_DECODER_ADDRESS) ERR_UNEXPECTED_ACTION)
		;; Ensure that the action is authorized
		(try! (check-update-source (get emitter-chain vaa) (get emitter-address vaa)))
		;; Ensure that the latest wormhole contract is used
		(try! (expect-active-wormhole-contract wormhole-core-contract expected-execution-plan))
		;; Update execution plan
		(let ((updated-data (try! (parse-principal (get body ptgm)))))
			(var-set current-execution-plan (merge expected-execution-plan { pyth-decoder-contract: updated-data }))
			;; Emit event
			(print { type: "pyth-decoder-contract", action: "updated", data: updated-data })
			(ok updated-data))))

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

**File:** local-testing/contracts/pyth/unit-tests/pyth/ptgm.test.ts (L580-597)
```typescript
    res = simnet.callPublicFn(
      pythOracleContractName,
      `verify-and-update-price-feeds`,
      [Cl.bufferFromHex("00"), Cl.tuple(executionPlanBase)],
      sender,
    );
    expect(res.result).toBeErr(Cl.uint(4003));

    res = simnet.callPublicFn(
      pythOracleContractName,
      `read-price-feed`,
      [
        Cl.bufferFromHex("00"),
        Cl.contractPrincipal(deployer, pythStorageContractName),
      ],
      sender,
    );
    expect(res.result).toBeErr(Cl.uint(4003));
```
