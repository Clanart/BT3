### Title
Unbounded decimal-string parsing in legacy `json` precompile's `ExtractAsUint256` allows CPU/memory DoS via historical `eth_call` - ([File: precompiles/json/legacy/{v552,v555,v562,v603,v605,v606,v610,v614,v620,v630,v640}/json.go])

### Summary
Eleven of the thirteen versioned implementations of the `json` precompile's `ExtractAsUint256` method omit a length bound on the numeric string before calling `big.Int.SetString`, allowing an attacker-controlled JSON payload with an arbitrarily long digit string to trigger expensive big-integer parsing.

### Finding Description
The `json` precompile's `ExtractAsUint256` extracts a value from caller-supplied JSON bytes and converts it to a `big.Int` via `new(big.Int).SetString(strValue, 10)`. The current/latest implementation in `precompiles/json/json.go` bounds the string length (`len(strValue) > 100`) before parsing: [1](#0-0) 

The same length check exists in the two newest legacy versions, `v65` and `v66`: [2](#0-1) 

However, eleven older legacy versions — `v552`, `v555`, `v562`, `v603`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640` — perform the exact same `SetString(strValue, 10)` call with **no length check at all**, e.g.: [3](#0-2) 

All versions, including these unbounded ones, remain wired into the live precompile dispatch table in `precompiles/json/setup.go`, keyed by the chain-upgrade version under which they were active: [4](#0-3) 

Because Sei's EVM precompile dispatch is versioned by the upgrade name active at a given block height (used to reproduce historical execution semantics, e.g. for `eth_call`/`eth_estimateGas` against an old `blockNumber`, or tracing/replay of historical transactions), any public JSON-RPC client can select a target block whose active upgrade corresponds to one of the unbounded legacy versions and invoke the `json` precompile's `extractAsUint256` method with a crafted JSON string containing a decimal number with millions of digits, forcing the node to perform proportional big-integer parsing.

### Impact Explanation
An attacker can send `eth_call` (or `eth_estimateGas`/tracing) requests targeting historical block heights corresponding to any of the eleven vulnerable upgrade versions, supplying a JSON blob with an oversized numeric field to the `json` precompile. This forces the default-configuration public RPC node to perform unbounded big-integer parsing (`big.Int.SetString`) repeatedly and cheaply (calls can be free/gas-metered but bounded EVM gas does not directly bound Go-level CPU/memory cost of a single native SetString call proportional to input size), degrading node responsiveness and potentially causing a crash or hang of the RPC node under repeated/concurrent requests. This matches the "crash of default-configuration RPC nodes" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate: the attack requires no privileges, only a public RPC endpoint and an `eth_call` targeting a historical block height where one of the eleven unguarded legacy `json` precompile versions was active — any past state on an archive/full node is reachable this way. The payload construction (large JSON string with an oversized numeric field, under 24KB-ish general calldata/JSON size limits if any) is trivial.

### Recommendation
Backport the length check (`len(strValue) > 100`, matching `precompiles/json/json.go` and the `v65`/`v66` versions) to all legacy `ExtractAsUint256` implementations that currently lack it (`v552`, `v555`, `v562`, `v603`, `v605`, `v606`, `v610`, `v614`, `v620`, `v630`, `v640`), or alternatively enforce the same bound centrally before dispatching to any versioned precompile implementation, so historical-block execution paths cannot be abused to bypass the fix applied only to the latest version.

### Proof of Concept
1. Identify a historical block height whose active chain upgrade corresponds to one of the vulnerable versions (e.g., `v6.4.0`, which maps to `jsonv640` per `precompiles/json/setup.go` line 36).
2. Send an `eth_call` (or equivalent RPC method allowing a historical `blockNumber` parameter) to the `json` precompile address, invoking `extractAsUint256(bytes,string)` with a JSON payload such as `{"k": <10_000_000-digit number>}` and key `"k"`.
3. Observe that the vulnerable implementation, e.g. `precompiles/json/legacy/v640/json.go` lines 170-182, performs `new(big.Int).SetString(strValue, 10)` directly on the oversized string with no prior length check, unlike the current implementation which rejects strings over 100 characters.
4. Repeat the call concurrently to amplify CPU/memory load on the target public RPC node. [5](#0-4) [4](#0-3)

### Citations

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

**File:** precompiles/json/legacy/v66/json.go (L173-185)
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

**File:** precompiles/json/legacy/v640/json.go (L164-182)
```go
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

**File:** precompiles/json/setup.go (L23-40)
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
}
```
