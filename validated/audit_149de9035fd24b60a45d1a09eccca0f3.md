Confirmed: `EVMTransaction` in `x/evm/keeper/msg_server.go` has a top-level `recover()` that only logs and **re-panics** (`panic(pe)`) rather than converting the panic into a reverted transaction. This means any unrecovered Go panic raised deep inside precompile execution propagates all the way up through the message server and is not treated as an ordinary EVM revert.

### Title
Malicious CW20/CW721/CW1155 contract can crash validator nodes via unchecked type assertion in the `pointer` precompile - (File: `precompiles/pointer/pointer.go`)

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` handlers query an attacker-supplied CosmWasm contract (`token_info` / `contract_info`) and blindly type-assert the JSON response fields `name`/`symbol` as Go strings without validating their type, analogous to CVE-2017-9443's flaw of trusting untrusted structured input (a "crafted tables object" in a package manifest) without validation before it reaches a sensitive operation.

### Finding Description
`AddCW20` (and the CW721/CW1155 variants) call `p.wasmdKeeper.QuerySmart`/`QuerySmartSafe` against a contract address fully controlled by the calling EVM account (any contract deployer can point this at their own malicious CW20/CW721/CW1155 contract), unmarshal the JSON response into `map[string]interface{}`, and then do: [1](#0-0) 
```
res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
...
formattedRes := map[string]interface{}{}
if err := json.Unmarshal(res, &formattedRes); err != nil { ... }
name := formattedRes["name"].(string)
symbol := formattedRes["symbol"].(string)
```
A CosmWasm contract is free to return any JSON it wants for its `token_info`/`contract_info` query — including a `name` or `symbol` field that is a number, bool, object, array, or simply absent. Go's unchecked type assertion `x.(string)` panics when `x` is not a `string` (or is `nil`, i.e. field missing entirely). This same unguarded pattern repeats across every historical version of the pointer precompile bundled in the binary (`precompiles/pointer/legacy/v562`, `v600`, `v605`, `v606`, `v610`, `v614`, `v620`, `v640`, `v66`, `v67`), all reachable depending on the chain-upgrade height selected by callers.

Unlike the framework's designed "gas-panic → revert" handling, `TestDynamicGasPrecompileRepanicsNonGas` in `precompiles/common/precompiles_test.go` explicitly documents that **non-gas panics are intentionally NOT converted to reverts** and must propagate: [2](#0-1) 

The panic then propagates out of `RunAndCalculateGas` into `EVMTransaction`, whose top-level recover only logs and re-panics: [3](#0-2) 

### Impact Explanation
Because the recover in `EVMTransaction` deliberately re-panics instead of converting the failure into a normal transaction error, the panic is expected to escape further up the call stack (into the ABCI/BaseApp message-handling layer). If no outer recover exists at that layer for this specific panic pattern, the panic can crash the node process handling the transaction. Since every honest validator executing the same deterministic transaction would encounter the identical panic, this is a low-cost, permissionless way for any address (no special privilege required — anyone can deploy a CW20/CW721/CW1155 contract and call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer`) to attempt to crash all validators processing that block, which would manifest as a chain-wide halt/DoS rather than an isolated single-node crash.

### Likelihood Explanation
High from a reachability standpoint: creating a minimal CosmWasm contract whose `token_info`/`contract_info` query handler returns `{"name": 123, "symbol": "X", ...}` (or omits the `name`/`symbol` keys) is trivial, and calling the public `pointer` precompile method (`addCW20Pointer`, `addCW721Pointer`, `addCW1155Pointer`) at address `0x...100b` from any EVM account is unauthenticated and permissionless. The exact ultimate consequence (whether ABCI-layer recovery exists above `EVMTransaction` to contain the panic to just the failing transaction) could not be fully confirmed with the tools available in this session — only the `msg_server.go` recover behavior was directly inspected, and it explicitly re-panics rather than swallowing the error.

### Recommendation
Replace unchecked type assertions (`formattedRes["name"].(string)`) with safe, checked type assertions (`name, ok := formattedRes["name"].(string); if !ok { return nil, 0, fmt.Errorf(...) }`) in `AddCW20`, `AddCW721`, and `AddCW1155` across `precompiles/pointer/pointer.go` and all legacy versioned copies, so malformed/malicious CW20/CW721/CW1155 query responses produce a normal EVM revert instead of an unrecovered Go panic.

### Proof of Concept
1. Deploy a CosmWasm contract whose `token_info` query handler (for CW20) returns JSON such as `{"name": 12345, "symbol": "ABC", "decimals": 6, "total_supply": "0"}` (numeric `name` instead of string), or simply omits the `name` field entirely.
2. From any EVM account, call `addCW20Pointer(cw20ContractAddress)` on the `pointer` precompile at `0x000000000000000000000000000000000000100b`.
3. `AddCW20` executes `p.wasmdKeeper.QuerySmartSafe(...)`, unmarshals the crafted JSON, then executes `formattedRes["name"].(string)`, which panics because the JSON value is not a Go `string`.
4. The panic propagates out of `Execute`/`RunAndCalculateGas` into `EVMTransaction`'s deferred recover, which logs and re-panics (`x/evm/keeper/msg_server.go:82-89`), escaping message-server-level containment.

### Citations

**File:** precompiles/pointer/pointer.go (L141-156)
```go
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
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

**File:** precompiles/common/precompiles_test.go (L140-158)
```go
// TestDynamicGasPrecompileRepanicsNonGas verifies that only gas-meter panics are
// converted to reverts: a non-gas panic must propagate rather than be masked.
func TestDynamicGasPrecompileRepanicsNonGas(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx(nil)
	abiBz, err := os.ReadFile("erc20_abi.json")
	require.Nil(t, err)
	newAbi, err := abi.JSON(bytes.NewReader(abiBz))
	require.Nil(t, err)
	input, err := newAbi.Pack("decimals")
	require.Nil(t, err)

	precompile := common.NewDynamicGasPrecompile(newAbi, &MockDynamicGasPrecompileExecutor{panicWith: "boom", evmKeeper: k}, ethcommon.Address{}, "test")
	stateDB := state.NewDBImpl(ctx.WithEventManager(sdk.NewEventManager()), k, false)
	// Ample gas so the decode charges pass and the (panicking) executor runs.
	require.PanicsWithValue(t, "boom", func() {
		_, _, _ = precompile.RunAndCalculateGas(&vm.EVM{StateDB: stateDB}, ethcommon.Address{}, ethcommon.Address{}, input, 100000, big.NewInt(0), nil, false, false)
	})
}
```

**File:** x/evm/keeper/msg_server.go (L80-89)
```go
	defer func() {
		defer stateDB.Cleanup()
		if pe := recover(); pe != nil {
			if !strings.Contains(fmt.Sprintf("%s", pe), occtypes.ErrReadEstimate.Error()) {
				debug.PrintStack()
				logger.Error("EVM PANIC", "err", pe)
				evmKeeperMetrics.panics.Add(goCtx, 1)
			}
			panic(pe)
		}
```
