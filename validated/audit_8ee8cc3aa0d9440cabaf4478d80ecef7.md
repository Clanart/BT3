### Title
Unbounded decimal-string length in the legacy JSON precompile's `extractAsUint256` causes quadratic-cost `big.Int.SetString` parsing, reachable via public `debug_trace*` RPCs - ([File: precompiles/json/legacy/v640/json.go])

### Summary
Several legacy versions of the `json` Cosmos precompile's `ExtractAsUint256` method convert an attacker-controlled JSON numeric string directly into a `big.Int` with `new(big.Int).SetString(strValue, 10)` and impose **no bound on the length of `strValue`**. The currently-active precompile version (`precompiles/json/json.go`) was patched to reject strings longer than 100 characters, but every legacy snapshot below `v6.6` (`v552, v555, v562, v603, v605, v606, v610, v614, v620, v630, v640, v65`) still lacks this check. These legacy snapshots are not dead code: `Keeper.CustomPrecompiles` dispatches to them whenever `ctx.IsTracing()` is true, which is exactly the code path exercised by the public `debug_traceTransaction` / `debug_traceBlockByNumber` / `debug_traceBlockByHash` JSON-RPC endpoints when replaying historical transactions.

### Finding Description
`ExtractAsUint256` in the legacy precompile versions: [1](#0-0) 
parses `strValue` — a string taken verbatim from user-supplied JSON calldata — with `new(big.Int).SetString(strValue, 10)` with no length limit. This is unlike the currently active precompile, which was hardened with an explicit `len(strValue) > 100` guard: [2](#0-1) 

The version-selection logic in `Keeper.CustomPrecompiles` reveals when the vulnerable legacy code is actually invoked: [3](#0-2) 
When `ctx.IsTracing()` is true (i.e. any `debug_trace*` RPC call that replays a historical block/transaction), the keeper resolves the exact precompile version that was active at that historical height via `GetCustomPrecompilesVersions`: [4](#0-3) 
and `precompiles/json/setup.go` wires all the vulnerable legacy versions into that version map: [5](#0-4) 

This mirrors the CVE-2018-20699 bug class: a user-controlled, unbounded numeric value is fed into resource-intensive parsing logic (`cpuset` parsing in Docker vs. `big.Int.SetString` base-10 decimal parsing here) without a sanity bound, so the daemon/RPC process performs disproportionate CPU/memory work per request.

### Impact Explanation
Any Sei EVM transaction mined while a pre-`v6.6` precompile version was active could call the JSON precompile's `extractAsUint256` method with a very long decimal digit string (bounded only by tx/calldata size, tens of KB). That transaction is now permanently recorded on-chain. Every subsequent, unprivileged `debug_traceTransaction`/`debug_traceBlock*` JSON-RPC call against that historical block/transaction re-executes the legacy precompile path and re-triggers the unbounded `big.Int.SetString` decimal conversion — an operation with known quadratic-time cost for large digit counts in Go's `math/big` (the class of issue fixed upstream by Go's own DoS hardening in `math/big`, analogous to CVE-2022-23772). A public RPC client can therefore repeatedly invoke this trace call to consume disproportionate CPU/memory on a default-configuration RPC node, degrading or crashing it — satisfying the "crash of default-configuration RPC nodes" impact bar.

### Likelihood Explanation
Likelihood is limited by two conditions that must both hold: (1) a transaction that calls `extractAsUint256` with an oversized numeric string must exist in a block from before the `v6.6` upgrade (an attacker fully controls this — they can create it as an unprivileged EVM contract caller), and (2) the chain must still serve `debug_trace*` for that historical range. Since `debug_trace*` endpoints are commonly exposed on public archive/RPC nodes and the precompile call itself costs only ordinary EVM gas to include on-chain (no privilege required), an attacker can pre-plant such a transaction once and then repeatedly grief RPC infrastructure indefinitely by requesting traces on it.

### Recommendation
Backport the `len(strValue) > 100` (or an equivalent bound tied to `math/big`'s safe conversion limits) guard into every legacy `ExtractAsUint256` implementation used for `ctx.IsTracing()` replay (`precompiles/json/legacy/v552` through `v640`, `v65`), or alternatively cap/charge additional gas proportional to `len(strValue)^2` (or reject inputs above a safe threshold) before calling `big.Int.SetString` in the tracing dispatch path, so that historical trace replay cannot be abused to force disproportionate parsing cost regardless of which historical precompile snapshot is selected.

### Proof of Concept
1. Deploy a contract (or use an EOA call) that invokes the `json` precompile's `extractAsUint256(bytes,string)` method with JSON payload `{"k":"<N digits>"}` where `N` is tens of thousands of decimal digits, at a block height where the active precompile version is one of the unpatched legacy snapshots (e.g. `v6.4.0`/`v640` or earlier, before the `v6.6` fix landed) — reference the vulnerable code path: [6](#0-5) .
2. Wait for the transaction to be mined (it will succeed or revert on other grounds, but the parsing work still executes during `Execute`).
3. As an unprivileged client, repeatedly call `debug_traceTransaction` on that transaction's hash via the public JSON-RPC endpoint; each call causes the node to reconstruct the historical precompile version via `GetCustomPrecompilesVersions` [4](#0-3)  and re-run the unbounded `big.Int.SetString(strValue, 10)` parse, consuming disproportionate CPU/memory per call.
4. Repeat the trace call in parallel/loop from a public client to exhaust RPC node resources.

### Citations

**File:** precompiles/json/legacy/v640/json.go (L148-182)
```go
}

func (p PrecompileExecutor) ExtractAsUint256(_ sdk.Context, _ *abi.Method, args []interface{}, value *big.Int) (*big.Int, error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, err
	}

	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		return nil, err
	}

	// type assertion will always succeed because it's already validated in p.Prepare call in Run()
	bz := args[0].([]byte)
	decoded := map[string]gjson.RawMessage{}
	if err := gjson.Unmarshal(bz, &decoded); err != nil {
		return nil, err
	}
	key := args[1].(string)
	result, ok := decoded[key]
	if !ok {
		return nil, fmt.Errorf("input does not contain key %s", key)
	}

	// Assuming result is your byte slice
	// Convert byte slice to string and trim quotation marks
	strValue := strings.Trim(string(result), "\"")

	// Convert the string to big.Int
	value, success := new(big.Int).SetString(strValue, 10)
	if !success {
		return nil, fmt.Errorf("failed to convert %s to big.Int", strValue)
	}

	return value, nil
}
```

**File:** precompiles/json/json.go (L185-197)
```go
	// Assuming result is your byte slice
	// Convert byte slice to string and trim quotation marks
	strValue := strings.Trim(string(result), "\"")

	if len(strValue) > 100 {
		return nil, fmt.Errorf("value string too long: got %d, max 100", len(strValue))
	}

	// Convert the string to big.Int
	value, success := new(big.Int).SetString(strValue, 10)
	if !success {
		return nil, fmt.Errorf("failed to convert %s to big.Int", strValue)
	}
```

**File:** giga/deps/xevm/keeper/keeper.go (L159-169)
```go
func (k *Keeper) CustomPrecompiles(ctx sdk.Context) map[common.Address]vm.PrecompiledContract {
	if !ctx.IsTracing() {
		return k.latestCustomPrecompiles
	}
	versions := k.GetCustomPrecompilesVersions(ctx)
	cp := make(map[common.Address]vm.PrecompiledContract, len(k.customPrecompiles))
	for addr, versioned := range k.customPrecompiles {
		cp[addr] = versioned[versions[addr]]
	}
	return cp
}
```

**File:** giga/deps/xevm/keeper/keeper.go (L171-196)
```go
func (k *Keeper) GetCustomPrecompilesVersions(ctx sdk.Context) map[common.Address]string {
	height := ctx.BlockHeight()
	cp := make(map[common.Address]string, len(k.customPrecompiles))
	for addr, versioned := range k.customPrecompiles {
		mostRecentUpgradeHeight := int64(0)
		noForkHistory := true
		for upgrade := range versioned {
			upgradeHeight := k.upgradeKeeper.GetDoneHeight(ctx, upgrade)
			if upgradeHeight != 0 {
				noForkHistory = false
			}
			if height < upgradeHeight {
				// requested height hasn't seen this upgrade version yet.
				continue
			}
			if upgradeHeight > mostRecentUpgradeHeight {
				mostRecentUpgradeHeight = upgradeHeight
				cp[addr] = upgrade
			}
		}
		if noForkHistory {
			cp[addr] = k.latestUpgrade
		}
	}
	return cp
}
```

**File:** precompiles/json/setup.go (L23-39)
```go
func GetVersioned(latestUpgrade string, keepers utils.Keepers) utils.VersionedPrecompiles {
	return utils.VersionedPrecompiles{
		latestUpgrade: check(NewPrecompile(keepers)),
		"v5.5.2":      check(jsonv552.NewPrecompile(keepers)),
		"v5.5.5":      check(jsonv555.NewPrecompile(keepers)),
		"v5.6.2":      check(jsonv562.NewPrecompile(keepers)),
		"v6.0.3":      check(jsonv603.NewPrecompile(keepers)),
		"v6.0.5":      check(jsonv605.NewPrecompile(keepers)),
		"v6.0.6":      check(jsonv606.NewPrecompile(keepers)),
		"v6.1.0":      check(jsonv610.NewPrecompile(keepers)),
		"v6.1.4":      check(jsonv614.NewPrecompile(keepers)),
		"v6.2.0":      check(jsonv620.NewPrecompile(keepers)),
		"v6.3.0":      check(jsonv630.NewPrecompile(keepers)),
		"v6.4.0":      check(jsonv640.NewPrecompile(keepers)),
		"v6.5":        check(jsonv65.NewPrecompile(keepers)),
		"v6.6":        check(jsonv66.NewPrecompile(keepers)),
	}
```
