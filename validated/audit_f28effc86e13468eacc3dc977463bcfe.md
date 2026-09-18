### Title
Type-assertion panic on malformed CW20/CW721/CW1155 metadata in the `pointer` precompile's `AddCW20`/`AddCW721`/`AddCW1155` - (File: precompiles/pointer/pointer.go)

### Summary
The `pointer` precompile (address `0x100b`), reachable by any EVM caller, upserts an ERC pointer for a given CW20/CW721/CW1155 contract by querying that contract's `token_info`/`contract_info` and unmarshalling the JSON response into a `map[string]interface{}`, then unconditionally type-asserting `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` with no `, ok` check and no presence/type validation. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
This mirrors the CVE-2018-19802 bug class: a "constructor"-style function (`new_aubio_notes` in aubio; here `AddCW20`/`AddCW721`/`AddCW1155`) unconditionally dereferences/consumes untrusted-input-derived data without checking it is well-formed first, causing a runtime crash instead of a graceful error.

Here, `cwAddr := args[0].(string)` is fully attacker-controlled (any bech32 string an EVM caller supplies to the precompile), and the target CW contract's query response is also attacker-controlled — the caller can point this at *any* self-deployed CosmWasm contract, not just a genuine CW20/CW721/CW1155. If that contract's `token_info`/`contract_info` query returns JSON that omits the `name`/`symbol` keys, or returns them as `null`/a non-string type (e.g., a number, object, or array), then:
- `formattedRes["name"]` returns `nil` (Go zero-value for a missing map key), and `nil.(string)` is a type assertion that panics with `interface conversion: interface {} is nil, not string`.
- Likewise if `name`/`symbol` are present but not JSON strings (e.g. a JSON number or object), the type assertion panics with `interface conversion: interface {} is float64, not string`.

Because `cwAddress, err := sdk.AccAddressFromBench32(cwAddr)` only validates the bech32 format, not that the target is a real CW20/721/1155 with a spec-compliant `token_info`/`contract_info` response, an attacker can deploy an arbitrary CosmWasm contract implementing `contract_info`/`token_info` with a missing or non-string `name`/`symbol` field, then call `pointer.addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` on it from a normal EVM transaction. There is no validation layer between the attacker-controlled JSON payload and the type assertion.

### Impact Explanation
Whether this constitutes a node crash (vs. an isolated EVM revert) depends on whether the surrounding EVM execution path recovers from Go panics. I was unable to confirm within this investigation whether `vm.EVM.Call`/`RunPrecompiledContract` (go-ethereum) or Cosmos SDK's `BaseApp` transaction execution wraps precompile execution in a `recover()` that converts the panic into a tx-level error versus letting it propagate and crash the node process. Similar precompiles in this codebase (e.g., `precompiles/distribution/distribution.go`, `precompiles/wasmd/wasmd.go`) do contain explicit `recover()` blocks, which suggests panic-recovery is a known and applied pattern elsewhere, but I could not verify that the `pointer`/`pcommon.DynamicGasPrecompile` execution path (`RunAndCalculateGas`) has an equivalent guard for this specific panic. If it is not recovered at any layer, an unprivileged EVM transaction sender could reliably crash validator/full nodes processing the block (denial of service, potential block delay/halt). If it is recovered by an outer layer (e.g. EVM's own internal panic-to-error handling for precompiles, or a Cosmos SDK ante/msg-server recover), the practical impact is downgraded to a reverted transaction (denial of pointer registration for that address) with no chain-level impact.

### Likelihood Explanation
High: exploitation requires only (1) deploying an ordinary CosmWasm contract that implements a `contract_info`/`token_info` query returning JSON without a string `name`/`symbol`, and (2) sending one EVM transaction calling `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` with that contract's bech32 address as the argument. No privileged access, governance, or validator collusion is required — this is available to any public-RPC/EVM client.

### Recommendation
Replace the unchecked type assertions with checked (`, ok`) assertions and return a proper `error` (as done elsewhere in this file with `pcommon.ValidateArgsLength`/`ValidateNonPayable`) when `name`/`symbol` are missing or not strings, in all three functions (`AddCW20`, `AddCW721`, `AddCW1155`) as well as in the legacy versions retained under `precompiles/pointer/legacy/*` that contain the same pattern. Additionally, verify the code adds a defensive `recover()` around precompile execution (or confirm one already exists upstream) so that any future similarly-shaped panic degrades to a transaction revert rather than a node crash.

### Proof of Concept
1. Deploy a CosmWasm contract `C` whose `contract_info`/`token_info` query handler returns `{"decimals": 6}` (omitting `name` and `symbol`), or `{"name": 123, "symbol": 456}` (wrong JSON types).
2. From any EVM account, call the `pointer` precompile at `0x000000000000000000000000000000000000100b`, method `addCW721Pointer(string)` (or `addCW20Pointer`/`addCW1155Pointer`), passing `C`'s bech32 address as the argument.
3. Execution reaches `formattedRes["name"].(string)` in `AddCW721`/`AddCW20`/`AddCW1155` and panics on the type assertion. [4](#0-3)

### Citations

**File:** precompiles/pointer/pointer.go (L146-156)
```go
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L178-188)
```go
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L210-220)
```go
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"contract_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```
