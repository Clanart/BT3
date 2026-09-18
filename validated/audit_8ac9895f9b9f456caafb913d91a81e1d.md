## Analysis

CVE-2023-24755 is a NULL-pointer dereference in a codec triggered by attacker-controlled/malformed input causing an unchecked assumption about data shape. The reachable Go analog in `sei-chain` is an **unchecked type assertion on untrusted external data**, which in Go panics exactly like a nil/NULL dereference when the assumption is violated.

The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` executors query an arbitrary CosmWasm contract (`token_info`/`contract_info`) and then blindly type-assert two fields to `string` without checking for existence or type: [1](#0-0) [2](#0-1) [3](#0-2) 

This same unchecked pattern is duplicated across every legacy version of the pointer precompile (v552 through v640), so it has existed for many upgrade cycles: [4](#0-3)  and similarly in v552/v555/v562/v575/v580/v600/v605/v606/v610/v614/v630/v640.

Unlike this precompile, other precompiles in the same package (`addr`, `oracle`, `params`, `solo`) explicitly wrap `Execute` with a `recover()` specifically to prevent a Go panic from leaking out of the precompile: [5](#0-4) [6](#0-5) 

The `pointer` precompile's `Execute`/`AddCW20`/`AddCW721`/`AddCW1155` have no such guard, and the shared dynamic-gas dispatcher that calls into the executor also has no `recover()` — its `defer` only calls `HandlePrecompileError`, not `recover()`: [7](#0-6) 

The project's own integration test suite documents this exact class of bug as a "consensus-relevant guard": a precompile that fails must surface as `execution reverted`, **never as a raw Go panic** ("a `panic occurred` trace would mean a consensus-relevant unhandled error path"): [8](#0-7) [9](#0-8) 

Any unprivileged user can deploy their own CW20/CW721/CW1155 contract whose `token_info`/`contract_info` query handler omits the `name`/`symbol` keys or returns them as non-string JSON values (numbers, objects, null), then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against it from an EVM transaction, triggering `interface conversion: interface {} is nil/int/... , not string` — an unrecovered runtime panic in the precompile execution path.

I was not able to fully confirm, given the remaining tool budget, whether this panic is always absorbed by an outer generic panic-recovery layer (e.g., the ABCI `DeliverTx`/baseapp recovery visible in `sei-cosmos/baseapp/recovery.go` and `app/legacyabci/recovery.go`) for mined transactions, versus whether it can escape uncaught on the `eth_call`/`eth_estimateGas`/`debug_traceCall` simulation code paths in `evmrpc/`, which may not route through the same msg-level recovery wrapper. That distinction determines whether the practical impact is "failed tx only" (low severity) or an actual crash/unhandled-panic surfacing at a public RPC node (meets the Medium bar). Given the project's own test suite treats *any* panic leak from a precompile as a "consensus-relevant" defect worth guarding against, this is presented as the closest, code-confirmed analog to the CVE's "crafted input triggers null/interface dereference causing crash" bug class.

### Title
Unchecked type assertion on untrusted CW20/CW721/CW1155 metadata causes panic in pointer precompile - (File: precompiles/pointer/pointer.go)

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` executors type-assert the `name`/`symbol` fields of an arbitrary CosmWasm contract's `token_info`/`contract_info` query response directly to `string` without checking existence or type, and unlike sibling precompiles (`addr`, `oracle`, `params`, `solo`), this precompile has no `recover()` guard anywhere on the call path.

### Finding Description
`formattedRes["name"].(string)` and `formattedRes["symbol"].(string)` in `precompiles/pointer/pointer.go` (and all `precompiles/pointer/legacy/*` copies) assume the queried contract always returns these fields as JSON strings. A CW20/CW721/CW1155 contract fully controlled by the calling user can return a `token_info`/`contract_info` response where `name`/`symbol` are absent (decoded as Go `nil`), a number, an object, or an array. The unchecked assertion then panics with an interface-conversion error, functionally equivalent to a NULL-pointer dereference triggered by a crafted/malformed input, as in the reference CVE. [1](#0-0) [2](#0-1) [3](#0-2) 

Neither `Execute` in this precompile nor the shared `DynamicGasPrecompile.RunAndCalculateGas` dispatcher installs a `recover()` to convert such a panic into a normal `execution reverted` error, unlike `addr`, `oracle`, `params`, and `solo`, which explicitly document this as required behavior ("Needed to catch gas meter panics"). [7](#0-6) [10](#0-9) 

### Impact Explanation
The project's own test documentation treats a Go panic leaking out of a precompile as consensus-relevant and explicitly guards against it elsewhere, indicating the maintainers consider this class of bug significant. [8](#0-7) 
If this panic is not absorbed by outer recovery on all call paths (particularly RPC-side simulation such as `eth_call`/`eth_estimateGas`/`debug_traceCall`), it can surface as an unhandled panic to a public RPC node rather than a clean revert, contrary to the invariant the codebase enforces for every other precompile.

### Likelihood Explanation
High: any unprivileged user can deploy a CW20/CW721/CW1155 contract with a trivial `token_info`/`contract_info` handler that omits or mistypes `name`/`symbol`, then call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` (or simulate the call via `eth_call`) against their own contract — no privileged role or state is required.

### Recommendation
Add safe map-lookup with `ok` checks (or `, ok := formattedRes["name"].(string)`) and return a descriptive error instead of asserting directly, and add the same `recover()`-based panic guard used in `addr`/`oracle`/`params`/`solo` to `pointer.Execute` (and to `DynamicGasPrecompile.RunAndCalculateGas`) so any future unexpected-shape data cannot escape as a raw panic.

### Proof of Concept
1. Deploy a CW20 contract whose `token_info` query handler returns `{"decimals":6}` (no `name`/`symbol` fields) or `{"name":123,"symbol":456}`.
2. From an EVM account, call `addCW20Pointer(cwAddr)` on the pointer precompile (`0x...100b`) either as a mined transaction or via `eth_call`.
3. Observe `formattedRes["name"].(string)` panics with `interface conversion: interface {} is nil, not string` (or similar), since no `ok`-check or `recover()` exists on this path, unlike other precompiles in the same package.

### Citations

**File:** precompiles/pointer/pointer.go (L150-156)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L182-188)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW721Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/pointer.go (L214-220)
```go
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW1155Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
```

**File:** precompiles/pointer/legacy/v620/pointer.go (L99-122)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
	for _, denomUnit := range metadata.DenomUnits {
		if denomUnit.Exponent > uint32(decimals) && denomUnit.Exponent <= math.MaxUint8 {
			decimals = uint8(denomUnit.Exponent)
			name = denomUnit.Denom
			symbol = denomUnit.Denom
			if len(denomUnit.Aliases) > 0 {
				name = denomUnit.Aliases[0]
			}
		}
```

**File:** precompiles/addr/legacy/v67/addr.go (L95-99)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, _ common.Address, _ common.Address, args []interface{}, value *big.Int, readOnly bool, _ *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
```

**File:** precompiles/oracle/legacy/v630/oracle.go (L84-90)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (bz []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
		}
	}()
```

**File:** precompiles/common/precompiles.go (L156-164)
```go
func (d DynamicGasPrecompile) RunAndCalculateGas(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, suppliedGas uint64, value *big.Int, hooks *tracing.Hooks, readOnly bool, isFromDelegateCall bool) (ret []byte, remainingGas uint64, err error) {
	operation := fmt.Sprintf("%s_unknown", d.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			fmt.Printf("precompile %s encountered error: %v\n", d.name, err)
			err = vm.ErrExecutionReverted
		}
	}()
```

**File:** integration_test/precompile_tests/README.md (L32-37)
```markdown
- **Error handling.** Bad input reverts, and out-of-gas failures never leak a
  Go panic — a consensus-relevant guard inherited from the legacy suite. (The
  precise OOG shape differs per precompile: bank/addr executors convert the
  gas-meter panic to `execution reverted`, while a starved staking call runs
  out inside the cosmos store layer and surfaces a location-tagged
  `out of gas` error instead.)
```

**File:** integration_test/precompile_tests/utils/precompileUtils.ts (L163-176)
```typescript
/**
 * The load-bearing legacy assertion: a precompile that runs out of gas mid-execution
 * must surface as a plain EVM "execution reverted" in traces — never as a Go panic
 * (a "panic occurred" trace would mean a consensus-relevant unhandled error path).
 */
export async function expectTraceRevertedNotPanicked(txHash: string): Promise<void> {
    const trace = await traceTransaction(txHash);
    const error = trace.error ?? '';
    expect(error, `trace of ${txHash} must carry an error`).to.not.equal('');
    expect(error, 'precompile failure must not surface as a panic').to.not.include('panic');
    expect(error, 'precompile failure must trace as an execution revert').to.include(
        'execution reverted',
    );
}
```

**File:** precompiles/params/params.go (L61-67)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (bz []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
		}
	}()
```
