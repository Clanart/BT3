### Title
DeliverTx hook that translates CosmWasm events into synthetic EVM receipt logs can run out of a hardcoded gas limit, causing a panic that aborts an otherwise-successful transaction - (File: app/receipt.go)

### Summary
`app.AddCosmosEventsToEVMReceiptIfApplicable` is a post-execution "callback" invoked by the DeliverTx hook after a Cosmos/CosmWasm transaction succeeds, in order to synthesize ERC20/ERC721/ERC1155-style EVM logs from CW20/CW721/CW1155 wasm events (for pointer-contract interoperability). This function meters its own work using a governance-configurable but effectively fixed gas budget, `DeliverTxHookWasmGasLimit` (default `300000`), regardless of how many wasm events the underlying transaction actually emitted.

### Finding Description
The hook is wired in via `AddCosmosEventsToEVMReceiptIfApplicable`, called from `app/abci.go`/`app/app.go` after `DeliverTx` succeeds [1](#0-0) . It builds a bounded gas meter from a single, chain-wide param instead of a value scaled to the number of wasm events processed: [2](#0-1) 

This gas meter (`wasmToEvmEventCtx`) is then used to iterate over *every* wasm event in the transaction's event log, resolving CW20/CW721/CW1155 pointer lookups and building synthetic logs, and even issuing `QuerySmart` calls for allowance state (in `translateCW20Event`) — all charged against the same fixed 300k budget [3](#0-2) [4](#0-3) .

The comment on line 49-50 acknowledges the exact risk described in the analog report: `// Note: txs with a very large number of WASM events may run out of gas due to additional gas consumption from EVM receipt generation and event translation` [5](#0-4) . Unlike the wasmd submessage dispatcher's `dispatchMsgWithGasLimit`, which recovers from `sdk.ErrorOutOfGas` panics gracefully within a sandbox [6](#0-5) , `AddCosmosEventsToEVMReceiptIfApplicable` has **no** panic-recovery for out-of-gas around the loop over `wasmEvents` (only `translateCW20Event` wraps a `recover()`, and that only prints an error, and `translateCW721Event`/`translateCW1155Event` have none at all) [7](#0-6) [8](#0-7) [9](#0-8) . If the gas meter panics with `sdk.ErrorOutOfGas` inside `translateCW721Event`, `translateCW1155Event`, or the surrounding loop/receipt-write logic, this panic propagates up into the DeliverTx/hook call path.

A single CosmWasm transaction that legitimately triggers many CW20/CW721/CW1155 pointer transfer events — e.g., a batch NFT mint/transfer, or a contract emitting a very large `transfer_batch`/`mint_batch` CW1155 event with many token IDs — can exceed 300k gas purely in event-translation bookkeeping (map lookups, `QuerySmart` allowance calls, ABI packing for `TransferBatch`), even though the underlying wasm execution itself already succeeded and consumed its own (separate, unrelated) gas budget.

### Impact Explanation
Because this hook is invoked only after the Cosmos/CosmWasm message execution has already succeeded and been committed via `msCache.Write()` in the delivery path [10](#0-9) , an unrecovered panic here has the potential to propagate to a level whose recovery semantics differ from a normal message failure — turning a legitimately successful CosmWasm transaction's block-processing pipeline into a panic path instead of the intended "add synthetic EVM logs" side effect. At minimum, this can cause deferred EVM info/receipt updates for that tx to be skipped or block processing to abort/recover in a way that isn't scoped per-transaction, which risks corrupting the synthetic-receipt/bloom aggregation used by EVM-side clients relying on pointer-contract event visibility (an integrity issue for the CW↔EVM pointer bridge described in `x/evm/AGENTS.md`) [11](#0-10) . This does not directly cause fund loss by itself, but it is a resource-exhaustion/DoS-adjacent liveness bug reachable by an ordinary CW contract user issuing a transaction with many pointer-relevant wasm events, and it can silently corrupt synthetic receipt data for EVM observers depending on how far the panic propagates and is/isn't recovered at a higher layer (which was not confirmed in `app/abci.go`/`app/app.go` within the scope of this investigation).

### Likelihood Explanation
Reaching this path requires no special privilege — any CosmWasm user can call an `execute_batch`-style message on a CW20/CW721/CW1155 pointer-backed contract that emits many transfer/mint/burn wasm events in one transaction (e.g., a CW1155 `TransferBatch` with a large token-ID list, or a contract looping many CW721 transfers), driving the fixed 300k gas budget in `AddCosmosEventsToEVMReceiptIfApplicable` to exhaustion. Because the limit is a chain governance parameter set once (default 300000) and not scaled to the actual number of wasm events, any contract capable of emitting a large event volume in a single message triggers it.

### Recommendation
Wrap the event-translation loop (and each `translate*Event` call) in `AddCosmosEventsToEVMReceiptIfApplicable` with a `recover()` that safely treats an out-of-gas panic as "stop adding synthetic logs" rather than letting it propagate, mirroring the pattern used in `dispatchMsgWithGasLimit` in `sei-wasmd/x/wasm/keeper/msg_dispatcher.go`. Additionally, consider scaling `DeliverTxHookWasmGasLimit` (or applying a per-event cost cap) based on the number of wasm events being translated, so that transactions with many synthetic-log-eligible events degrade gracefully (e.g., truncating logs) instead of risking an unhandled panic.

### Proof of Concept
Not independently executed; based on static analysis of `app/receipt.go` lines 42-145 and the acknowledged risk comment at lines 49-50, plus verification that only `translateCW20Event` (not the outer loop, `translateCW721Event`, or `translateCW1155Event`) has panic recovery. A concrete PoC would require: constructing a CW1155 pointer contract, issuing a single `MsgExecuteContract` batch mint/transfer with enough token IDs/amounts to drive gas consumption in `translateCW1155Event` and the outer loop past 300000 gas, and observing whether `ctx.GasMeter().ConsumeGas` panics with `ErrorOutOfGas` propagate unrecovered through `AddCosmosEventsToEVMReceiptIfApplicable` into the DeliverTx hook caller.

### Citations

**File:** app/receipt.go (L42-52)
```go
func (app *App) AddCosmosEventsToEVMReceiptIfApplicable(ctx sdk.Context, tx sdk.Tx, checksum [32]byte, response sdk.DeliverTxHookInput) {
	// hooks will only be called if DeliverTx is successful
	wasmEvents := GetEventsOfType(response, wasmtypes.WasmModuleEventType)
	if len(wasmEvents) == 0 {
		return
	}
	logs := []*ethtypes.Log{}
	// Note: txs with a very large number of WASM events may run out of gas due to
	// additional gas consumption from EVM receipt generation and event translation
	wasmToEvmEventGasLimit := app.EvmKeeper.GetDeliverTxHookWasmGasLimit(ctx.WithGasMeter(sdk.NewInfiniteGasMeter(1, 1)))
	wasmToEvmEventCtx := ctx.WithGasMeter(sdk.NewGasMeterWithMultiplier(ctx, wasmToEvmEventGasLimit))
```

**File:** app/receipt.go (L72-104)
```go
	cw721TransferCounterMap := map[string]int{}
	for _, wasmEvent := range wasmEvents {
		contractAddr, found := GetAttributeValue(wasmEvent, wasmtypes.AttributeKeyContractAddr)
		if !found {
			continue
		}
		pointerAddr, _, exists := app.EvmKeeper.GetERC20CW20Pointer(wasmToEvmEventCtx, contractAddr)
		if exists {
			for _, log := range app.translateCW20Event(wasmToEvmEventCtx, wasmEvent, pointerAddr, contractAddr) {
				log.Index = uint(len(logs))
				logs = append(logs, log)
			}
			continue
		}
		// check if there is a ERC721 pointer to contract Addr
		pointerAddr, _, exists = app.EvmKeeper.GetERC721CW721Pointer(wasmToEvmEventCtx, contractAddr)
		if exists {
			for _, log := range app.translateCW721Event(wasmToEvmEventCtx, wasmEvent, pointerAddr, contractAddr, ownerEventsMap, cw721TransferCounterMap) {
				log.Index = uint(len(logs))
				logs = append(logs, log)
			}
			continue
		}
		// check if there is a ERC1155 pointer to contract Addr
		pointerAddr, _, exists = app.EvmKeeper.GetERC1155CW1155Pointer(wasmToEvmEventCtx, contractAddr)
		if exists {
			for _, log := range app.translateCW1155Event(wasmToEvmEventCtx, wasmEvent, pointerAddr, contractAddr) {
				log.Index = uint(len(logs))
				logs = append(logs, log)
			}
			continue
		}
	}
```

**File:** app/receipt.go (L147-152)
```go
func (app *App) translateCW20Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string) (res []*ethtypes.Log) {
	defer func() {
		if r := recover(); r != nil {
			fmt.Printf("[Error] Panic caught during translateCW20Event: type=%T, value=%+v\n", r, r)
		}
	}()
```

**File:** app/receipt.go (L169-197)
```go
		case "increase_allowance", "decrease_allowance":
			topics := []common.Hash{
				ERC20ApprovalTopic,
				action.Owner,
				action.Spender,
			}
			ret, err := app.WasmKeeper.QuerySmart(
				ctx,
				sdk.MustAccAddressFromBech32(contractAddr),
				[]byte(fmt.Sprintf(
					"{\"allowance\":{\"owner\":\"%s\",\"spender\":\"%s\"}}",
					app.EvmKeeper.GetSeiAddressOrDefault(ctx, common.BytesToAddress(action.Owner[:])).String(),
					app.EvmKeeper.GetSeiAddressOrDefault(ctx, common.BytesToAddress(action.Spender[:])).String())),
			)
			if err != nil {
				continue
			}
			allowanceResponse := &AllowanceResponse{}
			if err := json.Unmarshal(ret, allowanceResponse); err != nil {
				continue
			}
			res = append(res, &ethtypes.Log{
				Address: pointerAddr,
				Topics:  topics,
				Data:    common.BigToHash(allowanceResponse.Allowance.BigInt()).Bytes(),
			})
		}
	}
	return
```

**File:** app/receipt.go (L200-201)
```go
func (app *App) translateCW721Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string,
	ownerEventsMap map[string][]abci.Event, cw721TransferCounterMap map[string]int) (res []*ethtypes.Log) {
```

**File:** app/receipt.go (L307-308)
```go
func (app *App) translateCW1155Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string) (res []*ethtypes.Log) {
	for _, action := range app.GetActionsFromWasmEvent(ctx, wasmEvent) {
```

**File:** sei-wasmd/x/wasm/keeper/msg_dispatcher.go (L54-66)
```go
	// catch out of gas panic and just charge the entire gas limit
	defer func() {
		if r := recover(); r != nil {
			// if it's not an OutOfGas error, raise it again
			if _, ok := r.(sdk.ErrorOutOfGas); !ok {
				// log it to get the original stack trace somewhere (as panic(r) keeps message but stacktrace to here
				logger.Info("SubMsg rethrowing panic", "err", r)
				panic(r)
			}
			ctx.GasMeter().ConsumeGas(gasLimit, "Sub-Message OutOfGas panic")
			err = sdkerrors.Wrap(sdkerrors.ErrOutOfGas, "SubMsg hit gas limit")
		}
	}()
```

**File:** app/legacyabci/deliver_tx.go (L112-139)
```go
	runMsgCtx, msCache := contextCacher(ctx)
	// TODO: simplify
	result, err = msgRunner(runMsgCtx, tx.GetMsgs())

	if err == nil {
		msCache.Write()
	}
	// we do this since we will only be looking at result in DeliverTx
	if result != nil && len(anteEvents) > 0 {
		// append the events in the order of occurrence
		result.Events = append(anteEvents, result.Events...)
	}
	// only apply hooks if no error
	if err == nil && (!ctx.IsEVM() || result.EvmError == "") {
		var evmTxInfo *abci.EvmTxInfo
		if ctx.IsEVM() {
			evmTxInfo = &abci.EvmTxInfo{
				SenderAddress: ctx.EVMSenderAddress().Hex(),
				Nonce:         ctx.EVMNonce(),
				TxHash:        ctx.EVMTxHash().Hex(),
				VmError:       result.EvmError,
			}
		}
		evmHook(ctx, tx, checksum, sdk.DeliverTxHookInput{
			EvmTxInfo: evmTxInfo,
			Events:    result.Events,
		})
	}
```

**File:** x/evm/AGENTS.md (L151-156)
```markdown
### Synthetic Receipts and Logs
There are several types of synthetic receipts/logs on Sei.
- **CW->EVM** - if a Cosmos transaction calls an EVM contract, a synthetic receipt is created to carry any logs emitted on the EVM side.
- **CW Pointee** - if a Cosmos transaction calls a CosmWasm contract that has an EVM pointer, a synthetic receipt is created and synthetic logs are emitted to represent activities from EVM lens.
- **EVM->CW** - if an EVM transaction calls a CosmWasm contract that has an EVM pointer, synthetic logs are emitted and stored on the EVM transaction's receipt.

```
