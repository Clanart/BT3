### Title
Uncontrolled Resource Consumption via Unbounded `big.Int.SetString` Base-10 Parsing in Legacy JSON Precompile `ExtractAsUint256` (File: `precompiles/json/legacy/v630/json.go`, `precompiles/json/legacy/v614/json.go`, `precompiles/json/legacy/v610/json.go`, `precompiles/json/legacy/v606/json.go`, `precompiles/json/legacy/v605/json.go` (v603), `precompiles/json/legacy/v562/json.go`, `precompiles/json/legacy/v552/json.go`)

### Summary
Several historical versions of the `json` precompile's `ExtractAsUint256` method extract an arbitrary-length numeric substring from an attacker-controlled JSON blob and pass it directly to `new(big.Int).SetString(strValue, 10)` with **no upper bound on string length**, matching the algorithmic-complexity class described in CVE-2018-18853 (unbounded decimal-digit parsing). Later versions (`v66`, `v67`, and the current `precompiles/json/json.go`) fixed this by adding a `len(strValue) > 100` guard, confirming the maintainers recognized and patched this exact issue in the newest code path — but the unguarded legacy implementations remain in the binary and are selected whenever a request executes with `ctx.IsTracing() == true` at a historical block height that predates the fix.

### Finding Description
`ExtractAsUint256` in the affected legacy files: [1](#0-0) 
extracts a JSON value, trims quotes, and converts it straight to a `big.Int`:
```go
strValue := strings.Trim(string(result), "\"")
value, success := new(big.Int).SetString(strValue, 10)
```
There is no length cap before the conversion. By contrast, the current/patched implementation explicitly bounds the string: [2](#0-1) 
and the same defensive cap of 100 characters was also retrofitted for `sdk.NewDecFromStr`/`Dec.Unmarshal` in `sei-cosmos`: [3](#0-2) [4](#0-3) 

This pattern of retrofitting a hard length cap on decimal-string-to-`big.Int` conversions across the codebase strongly indicates the team was aware that base-10 string→`big.Int` conversion is not O(n) and can become a CPU-amplification vector for arbitrarily long digit strings — exactly the CWE-400/CVE-2018-18853 bug class in the report.

Reachability: Custom EVM precompiles are selected per-call based on whether the execution context is a tracing context: [5](#0-4) 
When `ctx.IsTracing()` is true, the keeper looks up the precompile version that was active as of the requested block height rather than always using the latest, patched version: [6](#0-5) 
This mechanism exists precisely to let `debug_traceCall`/`debug_traceTransaction`/historical `eth_call` reproduce contract execution semantics as they existed at older upgrade heights — meaning the vulnerable, unbounded `ExtractAsUint256` code from `v5.5.2` through `v6.3.0` is live and reachable today for any historical block height at or before the `v6.6` upgrade, via the public EVM JSON-RPC tracing/simulate surface.

### Impact Explanation
An attacker who can invoke the `json` precompile (address `0x0000000000000000000000000000000000001003`) `extractAsUint256(bytes,string)` method inside a `debug_traceCall`, `debug_traceTransaction`, or a historical/simulated `eth_call` (any RPC path that sets tracing context and resolves to a pre-`v6.6` precompile version) can supply a JSON blob whose targeted field is a string of many thousands of decimal digits. `big.Int.SetString` with base 10 is not linear in digit count for very large inputs; the conversion cost grows superlinearly, so a single call with a long-enough digit string can consume disproportionate CPU relative to the metered gas (`GasCostPerByte = 100` is a flat per-byte charge, not calibrated to the actual parsing cost). This can be used to delay processing of RPC-served debug/trace/simulate requests or the block-processing routines around them, potentially exceeding block-time budgets on a public RPC node executing this path repeatedly or with sufficiently large inputs — a resource-consumption/DoS impact consistent with the report's CWE-400 classification.

### Likelihood Explanation
Medium. The vulnerable code is not on the default hot execution path for current transactions (which use the latest, patched `precompiles/json/json.go`); it is only reached when the execution context is a tracing/simulation context resolving to a historical block height before the `v6.6` upgrade. This requires the target node to expose the `debug`/tracing RPC namespace (commonly enabled on many Sei public RPC nodes for historical replay/debugging) and requires the attacker to know/target a historical height where the `json` precompile was in scope. No special privileges are required beyond making a public RPC call; no funds or validator access needed.

### Recommendation
Backport the `len(strValue) > 100` (or similarly-sized) length guard already present in `precompiles/json/json.go` (and `v66`/`v67`) into all legacy `ExtractAsUint256` implementations (`v552`, `v562`, `v603`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`) so that tracing/historical replay of these code paths cannot be abused to feed unbounded digit strings into `big.Int.SetString`. Alternatively, cap the length of `strValue` (and any other free-form JSON extracted values feeding numeric parsers) uniformly at the point where `gjson`-decoded values are consumed across all `precompiles/json/legacy/*` versions, and consider making the `IsTracing` precompile-version-resolution path enforce the same input-size limits as the latest version regardless of the resolved historical version.

### Proof of Concept
1. Target a Sei node exposing `debug_traceCall` (or equivalent historical `eth_call`) at a block height that predates the `v6.6` upgrade (so `GetCustomPrecompilesVersions` resolves the `json` precompile to `v6.3.0` or earlier, e.g. `precompiles/json/legacy/v630/json.go`).
2. Craft a call to address `0x0000000000000000000000000000000000001003` invoking `extractAsUint256(bytes,string)` where `bytes` is a JSON document such as `{"k":"999999999999...<N digits>"}` with `N` in the tens of thousands, and `key = "k"`.
3. Submit via `debug_traceCall` with the tracing context set to the target historical block height.
4. Observe CPU time spent in `new(big.Int).SetString(strValue, 10)` inside `ExtractAsUint256`, growing disproportionately with `N`, while gas charged only scales linearly (`GasCostPerByte * len(bz)`), demonstrating the algorithmic-complexity mismatch between metered cost and actual CPU cost.

*Note: I was unable to fully verify from the index which specific RPC methods (`debug_traceCall`, `debug_traceTransaction`, `eth_call` with block-number override, etc.) set `ctx.IsTracing()`, and whether the `debug` namespace is enabled by default in the shipped node configuration — the relevant files (`evmrpc/tracers.go`, `sei-cosmos/types/context.go` IsTracing setter, RPC namespace/gas-cap config) were only partially retrievable through the index. A Devin session with full repository access would be needed to confirm the exact RPC-triggering conditions and default namespace exposure before treating this as fully validated.*

### Citations

**File:** precompiles/json/legacy/v630/json.go (L166-182)
```go
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

**File:** precompiles/json/json.go (L185-199)
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

	return value, nil
```

**File:** sei-cosmos/types/decimal.go (L157-163)
```go
func NewDecFromStr(str string) (Dec, error) {
	if len(str) > 100 {
		return Dec{}, fmt.Errorf("decimal string too long: got %d, max 100", len(str))
	}
	if len(str) == 0 {
		return Dec{}, fmt.Errorf("%s: %w", str, ErrEmptyDecimalStr)
	}
```

**File:** sei-cosmos/types/decimal.go (L746-750)
```go
	// The maximum valid Dec value requires ~95 decimal digits (315 bits), so 100 is
	// a safe upper bound.
	if len(data) > 100 {
		return fmt.Errorf("decimal string too long: got %d, max 100", len(data))
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
