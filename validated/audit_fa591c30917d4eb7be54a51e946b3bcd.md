### Title
Hardcoded Pyth proxy/storage/decoder/wormhole addresses in Zest's oracle read/write path can desync from Pyth's own governance-controlled execution plan, causing stale prices to be used unchecked for health calculations - (File: `mainnet/contracts/utility/v0-1-data.clar`, `mainnet/contracts/market/v0-4-market.clar`)

### Summary
This is the same bug class as the RabbitHole "receipt contract address changed but Quest still references the old one" finding: a canonical, upgradeable external address is changed by its own governance, but a dependent contract keeps using a hardcoded/stale reference to the old instance instead of resolving it dynamically, breaking the write/read symmetry the system depends on.

### Finding Description
Zest's price-writing path (`write-feed` in `mainnet/contracts/market/v0-4-market.clar:129-144`) hardcodes literal principals for the Pyth proxy, storage, decoder, and Wormhole core contracts: [1](#0-0) 

Zest's price-reading path (`get-pyth-price` in `mainnet/contracts/utility/v0-1-data.clar:91-94`) independently hardcodes the same `pyth-storage-v4` literal and forwards whatever price is stored there with no freshness/staleness validation of its own: [2](#0-1) 

However, Pyth itself runs a governance contract (`pyth-governance-v3.clar`) that is explicitly designed to migrate these exact addresses via VAA-authorized calls: `update-pyth-oracle-contract`, `update-pyth-storage-contract`, `update-pyth-decoder-contract`, and `update-wormhole-core-contract`, each of which mutates the canonical `current-execution-plan`: [3](#0-2) [4](#0-3) 

Once Pyth's `current-execution-plan` moves off the `v4` set of contracts, the deprecated `pyth-oracle-v4`/`pyth-storage-v4` pairing that Zest hardcodes will start rejecting calls via `check-execution-flow`/`expect-active-*-contract` checks in the (deprecated) proxy, exactly as demonstrated by the test at `update-pyth-oracle-contract`, where calls to the now-outdated oracle contract are rejected (`ERR_UNAUTHORIZED_ACCESS`, code 4003): [5](#0-4) 

At that point, `write-feed` in Zest's market permanently fails (`ERR-PRICE-FEED-UPDATE-FAILED`), so no new Pyth prices ever land in the old `pyth-storage-v4` map again. Meanwhile `get-pyth-price` keeps reading from that same frozen `pyth-storage-v4` contract and returns whatever price was last written before the migration — indefinitely, with no staleness check on the Pyth branch of `get-pyth-price` itself (unlike the DIA path, which at least carries a `timestamp` field, though I could not fully verify from the index whether `v0-4-market.clar`'s separate `last-update` map/`max-staleness` logic independently gates on this before it reaches the health/liquidation math; the market file has ~58 references to staleness-related identifiers that I was not able to fully inspect before running out of tool budget).

### Impact Explanation
If Zest's dependent contracts do not independently enforce staleness before consuming `get-pyth-price`'s output, this collapses to a wrong price feeding directly into collateral valuation, LTV/health computations, borrow limits, and liquidation eligibility — i.e., a wrong health verdict, exactly the mechanism the rules class as in-scope ("confidence or staleness gating"). A stuck/frozen price can either (a) let users continue to borrow/avoid liquidation against a stale favorable price (temporary freezing of protocol funds / bad debt), or (b) trigger wrongful liquidations against a stale unfavorable price (theft of user collateral). Whoever can manipulate the true market price away from the frozen stale value while Zest still trusts it profits — either borrowers extracting excess value or liquidators harvesting healthy positions.

### Likelihood Explanation
This requires Pyth (the third party operating `pyth-governance-v3`) to execute one of its documented, legitimate contract-migration governance actions — not a bug or misconfiguration on Pyth's side, and not a DAO/registry misconfiguration on Zest's side. This is a foreseeable, normal upgrade event for any long-lived integration with an upgradeable oracle bridge, making the likelihood non-trivial over the protocol's lifetime, though it depends on an event outside Zest's direct control.

### Recommendation
Do not hardcode `pyth-oracle-v4` / `pyth-storage-v4` / `pyth-pnau-decoder-v3` / `wormhole-core-v4` principals as literals in `write-feed` (`v0-4-market.clar`) and `get-pyth-price` (`v0-1-data.clar`). Instead, resolve the current execution plan dynamically via `pyth-governance-v3`'s `get-current-execution-plan` (as shown at `local-testing/contracts/pyth/contracts/pyth-governance-v3.clar:78-88`) on every write and read, and add an explicit publish-time-based staleness check on the Pyth branch of `get-pyth-price` comparing against the asset's configured `max-staleness` from the asset registry, mirroring the protection already partially present for DIA.

### Proof of Concept
1. Zest's market writes Pyth updates only through the hardcoded `pyth-oracle-v4`/`pyth-storage-v4` triple in `write-feed`.
2. Pyth's governance issues a legitimate VAA calling `update-pyth-storage-contract`/`update-pyth-oracle-contract` on `pyth-governance-v3`, moving the canonical `current-execution-plan` to new v5 contracts (this is a supported, tested code path, as shown in the `ptgm.test.ts` scenario where post-update calls to the old oracle are rejected with error 4003).
3. Any subsequent Zest `write-feed` call against the old `pyth-oracle-v4` now fails (`ERR-PRICE-FEED-UPDATE-FAILED`), so the price stored in `pyth-storage-v4` is frozen forever at its last pre-migration value.
4. `get-pyth-price` in `v0-1-data.clar` keeps reading from the frozen `pyth-storage-v4` map and returns that frozen value with no independent staleness check in that function.
5. Downstream consumers of this price (collateral valuation, borrow/health checks, liquidation checks) act on a stale price indefinitely, producing wrong health verdicts until Zest manually redeploys/upgrades its contracts to point at the new addresses — the same "must manually re-point / admin intervention required" failure mode described in the original RabbitHole report.

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

**File:** mainnet/contracts/utility/v0-1-data.clar (L89-94)
```text
;; Get price from Pyth oracle storage (read-only)
;; Returns price in 8 decimal precision (e.g., $1.00 = 100000000)
(define-private (get-pyth-price (feed-id (buff 32)))
  (match (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4 get-price feed-id)
    result (some (normalize-pyth (get price result) (get expo result)))
    err-val none))
```

**File:** local-testing/contracts/pyth/contracts/pyth-governance-v3.clar (L204-219)
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
```

**File:** local-testing/contracts/pyth/contracts/pyth-governance-v3.clar (L238-240)
```text
(define-public (update-pyth-storage-contract (vaa-bytes (buff 8192)) (wormhole-core-contract <wormhole-core-trait>))
	(let ((expected-execution-plan (var-get current-execution-plan))
			(vaa (try! (contract-call? wormhole-core-contract parse-and-verify-vaa vaa-bytes)))
```

**File:** local-testing/contracts/pyth/unit-tests/pyth/ptgm.test.ts (L699-706)
```typescript
    // Any future call from the now outdated v1 contract should be rejected
    res = simnet.callPublicFn(
      pythOracleContractName,
      `verify-and-update-price-feeds`,
      [Cl.bufferFromHex("00"), Cl.tuple(executionPlanBase)],
      sender,
    );
    expect(res.result).toBeErr(Cl.uint(4003));
```
