### Title
Unchecked type assertions on attacker-controlled CW20/CW721/CW1155 query responses cause a Go panic in the `pointer` precompile - ([File: precompiles/pointer/pointer.go])

### Summary
The `pointer` precompile's `AddCW20`, `AddCW721`, and `AddCW1155` executors query an arbitrary, caller-supplied CosmWasm contract address for its `token_info`/`contract_info`, unmarshal the JSON response into a `map[string]interface{}`, and then perform unchecked Go type assertions `formattedRes["name"].(string)` and `formattedRes["symbol"].(string)`. Any CosmWasm contract deployer can trivially make these fields anything other than a string (a number, a bool, `null`, or omit the key entirely, yielding a `nil` interface), which causes an unrecovered Go panic (`interface conversion: interface {} is <type>, not string`).

### Finding Description
`AddCW20` and `AddCW721` (and `AddCW1155`) call `p.wasmdKeeper.QuerySmartSafe` on an address fully controlled by the caller, unmarshal the raw JSON into `formattedRes`, and immediately do: [1](#0-0) [2](#0-1) 

There is no `, ok` check on either type assertion, and no validation that the queried contract actually implements the expected CW20/CW721 interface honestly. This exact unchecked-assertion pattern is repeated across every historical precompile version (`v552` through `v640`, plus the head `pointer.go`), per the grep results, so the flaw is not an isolated legacy artifact - it is present in the currently active implementation.

Unlike the rest of the precompile framework, which the codebase's own tests explicitly document as converting *gas-meter* panics to reverts while deliberately letting *non-gas* panics propagate (`TestDynamicGasPrecompileRepanicsNonGas`), and unlike the `common.Precompile.Run` wrapper, whose deferred `HandlePrecompileError` only inspects the named `err` return and never calls `recover()`: [3](#0-2) 
this type-assertion panic is not converted into an EVM revert. It propagates out of the go-ethereum interpreter, through `core.StateTransition`/`ApplyMessage`, and up into whichever tx-execution path invoked it.

### Impact Explanation
Because the panic is not caught inside the precompile or its `Run` wrapper, it surfaces as a raw Go panic during EVM message execution triggered by a single, ordinary, unprivileged transaction (any EOA calling `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` against a contract they deployed themselves). Depending on which execution path handles the transaction:
- In the legacy/V2 executor, this is handled by whatever recover wraps `DeliverTx`/`runMsgs`, and in the giga executor, `makeGigaDeliverTx`'s recover explicitly logs "panic in gigaDeliverTx" and falls back to `ErrPanic` for "nil deref from malformed protobuf"-style panics — i.e., it is designed to catch and convert unexpected panics into a failed-tx response rather than crash the process: [4](#0-3) 
- At the block level, `ProcessBlock` also wraps the entire block-processing pipeline in a recover, but treats any recovered panic as a fatal `err` that aborts the whole block (unless it matches the upgrade-panic regex), clearing all events and tx results for that block: [5](#0-4) 

Given these two different top-level recovery behaviors already exist in production code, this specific bug is very likely absorbed by one of those outer recovers in most configurations, downgrading it from a hard node crash to (at worst) a failed block-processing attempt that gets retried, or a reverted/failed single transaction. I could not fully trace, within the available tool budget, whether every code path that can reach `AddCW20`/`AddCW721`/`AddCW1155` (e.g. via `eth_call`/`eth_estimateGas` in `evmrpc`, or via the OCC-parallel giga executor's per-tx dispatch prior to reaching `ProcessBlock`'s or `gigaDeliverTx`'s recover) is uniformly protected, so I cannot rule out a path where this panic is unrecovered and crashes a node process (in particular default-configuration public RPC nodes serving `eth_call`/`eth_estimateGas`, whose own recover only guards the RPC handler goroutine and returns an error to the caller rather than corrupting consensus state, based on `SimulationAPI.Call`'s recover).

### Likelihood Explanation
Likelihood is high for triggering the panic itself: any address can deploy a CW20/CW721/CW1155-shaped contract whose `token_info`/`contract_info` query returns a non-string `name` or `symbol` field (or omits it), and then call the `pointer` precompile's `AddCW20Pointer`/`AddCW721Pointer`/`AddCW1155Pointer` method against it via a normal EVM transaction or `eth_call`. No special privileges are required. However, likelihood of this actually causing a *chain-halting* or *node-crashing* outcome (the bar required by the validation rules) is uncertain, because multiple layers of the stack (giga's `gigaDeliverTx` recover, `ProcessBlock`'s recover, RPC handler recovers) appear specifically designed to catch exactly this class of unexpected panic and convert it into a bounded error response instead of an unrecovered crash.

### Recommendation
Replace the unchecked type assertions with checked assertions (`v, ok := formattedRes["name"].(string)`) and return a normal `error` (e.g. "contract_info/token_info response missing or invalid 'name' field") instead of allowing a panic, in `precompiles/pointer/pointer.go`'s `AddCW20`, `AddCW721`, and `AddCW1155` (and ideally the historical `legacy/v*` copies if they remain reachable/registered). Additionally, add a `recover()`-based panic-to-revert conversion at the top of `PrecompileExecutor.Execute` in `precompiles/pointer/pointer.go`, consistent with the pattern already used in `precompiles/addr`, `precompiles/oracle`, and `precompiles/solo`, so that any future non-gas panic in this precompile is converted to `execution reverted` rather than propagating.

### Proof of Concept
1. Deploy (or use an existing) CW20-like CosmWasm contract whose `token_info` query handler returns JSON such as `{"name": 12345, "symbol": "T", "decimals": 6, "total_supply": "0"}` (a numeric `name` instead of a string), or simply omits the `name`/`symbol` keys entirely.
2. From any EVM account, call the `pointer` precompile at `0x000000000000000000000000000000000000100b` method `addCW20Pointer(cwAddr)` (or `addCW721Pointer`/`addCW1155Pointer` with an analogous malformed `contract_info` response) with the malicious contract's bech32 address as argument.
3. Execution reaches `formattedRes["name"].(string)` in `precompiles/pointer/pointer.go` (`AddCW20`/`AddCW721`/`AddCW1155`), which panics with `interface conversion: interface {} is float64, not string` (or `is nil, not string` if the key is missing) instead of returning an `error`.
4. Observe whether the enclosing executor (V2 `DeliverTx`, giga `gigaDeliverTx`, or `ProcessBlock`) recovers the panic as a failed transaction/aborted block, or whether it escapes uncaught — this final determination requires live-node testing beyond static analysis, since the reachable panic-to-crash path could not be conclusively confirmed from the indexed code alone.

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

**File:** precompiles/common/precompiles.go (L64-72)
```go
func (p Precompile) Run(evm *vm.EVM, caller common.Address, callingContract common.Address, input []byte, value *big.Int, readOnly bool, isFromDelegateCall bool, hooks *tracing.Hooks) (bz []byte, err error) {
	operation := fmt.Sprintf("%s_unknown", p.name)
	defer func() {
		HandlePrecompileError(err, evm, operation)
		if err != nil {
			err = vm.ErrExecutionReverted
		}
	}()
	ctx, method, args, err := p.Prepare(evm, input)
```

**File:** app/app.go (L1764-1781)
```go
func (app *App) ProcessBlock(ctx sdk.Context, txs [][]byte, req *BlockProcessRequest, lastCommit abci.CommitInfo, simulate bool, preDecoded []sdk.Tx) (events []abci.Event, txResults []*abci.ExecTxResult, endBlockResp abci.ResponseEndBlock, err error) {
	defer func() {
		if r := recover(); r != nil {
			panicMsg := fmt.Sprintf("%v", r)

			// Re-panic for upgrade-related panics to allow proper upgrade mechanism
			if upgradePanicRe.MatchString(panicMsg) {
				logger.Error("upgrade panic detected, panicking to trigger upgrade", "panic", r)
				panic(r) // Re-panic to trigger upgrade mechanism
			}
			stack := string(debug.Stack())
			logger.Error("panic recovered in ProcessBlock", "panic", r, "stack", stack)
			err = fmt.Errorf("ProcessBlock panic: %v", r)
			events = nil
			txResults = nil
			endBlockResp = abci.ResponseEndBlock{}
		}
	}()
```

**File:** app/app.go (L2126-2132)
```go
				// For other panics (e.g., nil deref from malformed protobuf), log and return ErrPanic
				logger.Error("panic in gigaDeliverTx", "panic", r, "stack", string(debug.Stack()))
				resp = abci.ResponseDeliverTx{
					Codespace: sdkerrors.UndefinedCodespace,
					Code:      sdkerrors.ErrPanic.ABCICode(),
					Log:       fmt.Sprintf("recovered: %v\nstack:\n%v", r, string(debug.Stack())),
				}
```
