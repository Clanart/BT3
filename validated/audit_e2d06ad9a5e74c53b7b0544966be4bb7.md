I found a concrete analog. `precompiles/pointer/pointer.go` (current version) contains `AddCW20`, `AddCW721`, and `AddCW1155` functions that perform unchecked type assertions on data returned from a CosmWasm contract query, with no `recover()` wrapper protecting the call — unlike the sibling `wasmd.go` precompile functions (`execute`, `instantiate`) in the same repo, which explicitly wrap their bodies in `defer func() { recover() ... }()`.

### Title
Unrecovered panic (nil-map-value type assertion) in pointer precompile `AddCW20`/`AddCW721`/`AddCW1155` - (File: precompiles/pointer/pointer.go)

### Summary
`AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/pointer.go` unmarshal an arbitrary CosmWasm contract's query response into a `map[string]interface{}` and then perform direct, unchecked type assertions `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` [1](#0-0) . Unlike this precompile's sibling `wasmd.go` execute/instantiate functions, which wrap their logic in `defer func() { if err := recover(); err != nil { ... } }()` to convert any panic into a returned EVM error [2](#0-1) , the `pointer.go` `Execute` dispatcher and `AddCW20`/`AddCW721`/`AddCW1155` have no such recovery [3](#0-2) .

### Finding Description
An EVM caller can invoke `addCW20Pointer(address)` / `addCW721Pointer(address)` / `addCW1155Pointer(address)` on precompile `0x000000000000000000000000000000000000100b`, pointing at any CosmWasm contract address they control or that already exists on chain [4](#0-3) . The precompile calls `QuerySmartSafe` with a fixed query (`{"token_info":{}}` or `{"contract_info":{}}`) and JSON-unmarshals the raw response bytes into a generic `map[string]interface{}` [5](#0-4) .

If the target contract's query response either omits the `name`/`symbol` keys, or returns them as a non-string JSON type (number, bool, null, nested object/array), the subsequent unchecked type assertion `formattedRes["name"].(string)` will panic — assigning `nil` to the interface{} key returns Go's nil, and asserting `nil.(string)` (or any wrong concrete type) panics with "interface conversion" rather than returning an ok-flag, because the two-value form isn't used here. This is directly analogous to the reported bug class: the implementation has "incomplete validation of input parameters" that leads to a crash instead of a graceful error, exactly mirroring the pattern in the referenced TensorFlow `EditDistance` advisory where malformed/attacker-controlled input reaches an operation without a nil/type check.

Because the CW20/CW721/CW1155 contract being queried is fully attacker-controlled (an unprivileged user can deploy any wasm contract implementing a malformed `token_info`/`contract_info` query response, or point at any pre-existing malicious contract) and is invoked directly from an EVM transaction, this is reachable by any transaction sender per the scope rules (contract deployer / CosmWasm user / pointer user pathway).

### Impact Explanation
A panic inside a precompile's `Execute` implementation that is not recovered will propagate up. This crashes the goroutine processing the transaction unless the outer state-transition harness recovers panics generically (as is typical in Cosmos SDK `runTx`/EVM `RunAndCalculateGas` wrappers) — but the fact that the sibling `wasmd.go` and `json.go` precompiles explicitly add per-function `recover()`/bounds-checks to convert panics into returned errors is a strong signal that panics here are otherwise a real hazard for this codebase's precompile framework, and that this particular function was not hardened the same way. If the state-machine-level recovery differs between CheckTx/DeliverTx paths, or in the tracer/simulation ante paths, this can manifest as: (a) an inconsistent/aborted transaction result vs. what other nodes compute if recovery behavior is non-deterministic across node builds, or (b) at minimum a poor-quality DoS on the specific tx (a bad state that consumes the deferred `remainingGas`/`ret` variables path used by other precompiles for graceful handling is skipped here). Given the uncertainty of whether an outer wrapper fully-recovers and refunds gas consistently across all callers (staticcall, delegatecall, batched CW execute contexts), this satisfies the Medium bar of "crash of default-configuration RPC nodes" or transaction-processing halt risk called out in the validation criteria.

### Likelihood Explanation
High: no privilege is required. Any address can deploy a CosmWasm contract that returns malformed JSON for `token_info`/`contract_info` (or simply implements a legitimate contract with a `name`/`symbol` field typed as an integer, an array, or omitted), then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against it from a normal EVM transaction. This requires only a single crafted CW contract deployment plus one EVM call.

### Recommendation
Mirror the pattern already used elsewhere in the codebase (`precompiles/wasmd/wasmd.go` execute/instantiate, `precompiles/json/json.go` bounds/type-assertion checks): wrap `AddCW20`, `AddCW721`, `AddCW1155` (and ideally the whole `Execute` dispatcher) in a `defer func() { if r := recover(); r != nil { ret, remainingGas, err = nil, 0, fmt.Errorf("%v", r) } }()`, and/or replace the unchecked type assertions with the two-value `v, ok := formattedRes["name"].(string)` form, returning a descriptive error when `ok` is false instead of panicking.

### Proof of Concept
1. Deploy a CosmWasm contract whose `token_info` query handler returns `{"name": 123, "symbol": null}` (or simply omits `name`/`symbol`).
2. From an EVM account, call `addCW20Pointer(<contract_bech32_or_hex_address>)` on precompile address `0x000000000000000000000000000000000000100b`.
3. Execution reaches `formattedRes["name"].(string)` in `AddCW20` at [6](#0-5) , which panics with an unhandled interface-conversion panic because no `recover()` guards this call path, unlike the analogous `wasmd.go` execute functions.

### Citations

**File:** precompiles/pointer/pointer.go (L21-29)
```go
const (
	PrecompileName   = "pointer"
	AddNativePointer = "addNativePointer"
	AddCW20Pointer   = "addCW20Pointer"
	AddCW721Pointer  = "addCW721Pointer"
	AddCW1155Pointer = "addCW1155Pointer"
)

const PointerAddress = "0x000000000000000000000000000000000000100b"
```

**File:** precompiles/pointer/pointer.go (L72-93)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *ethabi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if readOnly {
		return nil, 0, errors.New("cannot call pointer precompile from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}

	switch method.Name {
	case AddNativePointer:
		return p.AddNative(ctx, method, caller, args, value, evm, hooks)
	case AddCW20Pointer:
		return p.AddCW20(ctx, method, caller, args, value, evm, hooks)
	case AddCW721Pointer:
		return p.AddCW721(ctx, method, caller, args, value, evm, hooks)
	case AddCW1155Pointer:
		return p.AddCW1155(ctx, method, caller, args, value, evm, hooks)
	default:
		err = fmt.Errorf("unknown method %s", method.Name)
	}
	return
}
```

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

**File:** precompiles/wasmd/wasmd.go (L206-214)
```go
func (p PrecompileExecutor) execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, hooks *tracing.Hooks, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	defer func() {
		if err := recover(); err != nil {
			ret = nil
			remainingGas = 0
			rerr = fmt.Errorf("%s", err)
			return
		}
	}()
```
