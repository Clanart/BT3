Based on my research, I found a strong analog to CVE-2023-7042's bug class (a TLV/event-parsing function that dereferences a field without checking whether it was populated, causing a crash on malformed input) in Sei's CW1155→ERC1155 synthetic-event translation code.

### Title
Nil-pointer panic in `translateCW1155Event` batch handling from malformed CW1155 `token_ids`/`amounts` attributes - (File: app/receipt.go)

### Summary
`app.AddCosmosEventsToEVMReceiptIfApplicable` [1](#0-0)  parses Cosmos wasm events from any CW1155 contract that has a registered ERC1155 pointer and turns them into synthetic EVM logs via `translateCW1155Event` [2](#0-1) . For the `transfer_batch`/`mint_batch`/`burn_batch` actions, the code only checks that the `TokenIds`/`Amounts` slices are non-empty, but never validates that every individual element parsed successfully, before packing them with the ABI encoder.

### Finding Description
`GetActionsFromWasmEvent` builds `Action.TokenIds`/`Action.Amounts` by splitting the raw wasm-event attribute string on commas and converting each piece with `safeBigIntFromString`, which returns `nil` for any element that fails to parse as an integer: [3](#0-2)  and [4](#0-3) .

`translateCW1155Event`'s batch case only guards against an empty slice (`len(...) == 0`), not against individual `nil` entries, before calling `dataArgs.Pack(action.TokenIds, action.Amounts)`: [5](#0-4) . If any comma-separated `token_ids`/`amounts` element is not a valid integer (e.g. `"1,x,3"`), the corresponding slice element is a `nil *big.Int`. The go-ethereum ABI packer dereferences the `*big.Int` fields internally when encoding the uint256 array, causing a nil-pointer dereference panic. This directly parallels the kernel CVE where a TLV-derived structure could have a null field that is dereferenced without validation, causing a crash.

Unlike the sibling function `translateCW20Event`, which wraps its whole body in `defer recover()` [6](#0-5) , neither `translateCW721Event` [7](#0-6)  nor `translateCW1155Event` [8](#0-7)  has any panic recovery, so a panic here propagates out of the DeliverTx-success hook.

### Impact Explanation
Because the wasm events that drive this code are emitted deterministically by contract execution and processed identically by every node validating/replaying the block, an uncaught panic here would be hit by all validators executing the transaction, potentially producing a chain-wide halt rather than merely failing a single transaction. This matches the "validator halt" / DoS impact class named in scope.

### Likelihood Explanation
This path requires only: (1) a CW1155 contract with a registered ERC1155 pointer (creatable permissionlessly by any user through the pointer/CW1155 flow), and (2) that contract emitting a `transfer_batch`/`mint_batch`/`burn_batch` wasm event whose `token_ids` or `amounts` attribute contains a non-numeric element. I was not able to fully confirm from the index whether the CW1155 contract module itself always emits well-formed numeric batch attributes (which would make triggering this from a standard CW1155 implementation harder), nor could I fully verify whether an outer recover in the DeliverTx/ABCI pipeline (the hook is only invoked "if DeliverTx is successful" per the comment at [9](#0-8) ) would catch this panic before it escalates to a process crash. This uncertainty should be resolved by tracing the exact call site of `AddCosmosEventsToEVMReceiptIfApplicable` relative to `runTx`'s panic-recovery scope, and by checking whether any CW1155 contract can be coerced (e.g., via a custom/malicious CW1155 implementation registered as a pointer target) into emitting malformed batch attributes.

### Recommendation
In `translateCW1155Event`, validate that every element of `action.TokenIds` and `action.Amounts` is non-nil (mirroring the single-item nil-checks already used elsewhere in the file) before calling `dataArgs.Pack`, and skip/continue on any malformed entry. Additionally, wrap `translateCW721Event` and `translateCW1155Event` bodies in the same `defer recover()` pattern already used in `translateCW20Event` so that any unexpected panic during synthetic-event translation degrades gracefully instead of potentially crashing block processing.

### Proof of Concept
1. Deploy/associate a CW1155 contract with an ERC1155 pointer via the standard pointer-creation flow.
2. Cause the contract to emit a wasm event with `action=transfer_batch`, `token_ids="1,notanumber,3"`, `amounts="1,2,3"` (or with an analogous malformed `amounts` field).
3. When `AddCosmosEventsToEVMReceiptIfApplicable` → `translateCW1155Event` processes this event, `safeBigIntFromString("notanumber")` yields `nil`, `len(action.TokenIds) == 0` is false (3 elements), so the nil-check is bypassed, and `dataArgs.Pack(action.TokenIds, action.Amounts)` dereferences the `nil *big.Int`, panicking.
4. Because there is no `recover()` around `translateCW1155Event`, the panic propagates out of the post-DeliverTx hook.

### Citations

**File:** app/receipt.go (L42-47)
```go
func (app *App) AddCosmosEventsToEVMReceiptIfApplicable(ctx sdk.Context, tx sdk.Tx, checksum [32]byte, response sdk.DeliverTxHookInput) {
	// hooks will only be called if DeliverTx is successful
	wasmEvents := GetEventsOfType(response, wasmtypes.WasmModuleEventType)
	if len(wasmEvents) == 0 {
		return
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

**File:** app/receipt.go (L200-202)
```go
func (app *App) translateCW721Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string,
	ownerEventsMap map[string][]abci.Event, cw721TransferCounterMap map[string]int) (res []*ethtypes.Log) {
	for _, action := range app.GetActionsFromWasmEvent(ctx, wasmEvent) {
```

**File:** app/receipt.go (L307-309)
```go
func (app *App) translateCW1155Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string) (res []*ethtypes.Log) {
	for _, action := range app.GetActionsFromWasmEvent(ctx, wasmEvent) {
		switch action.Type {
```

**File:** app/receipt.go (L346-353)
```go
			if len(action.TokenIds) == 0 {
				continue
			}
			if len(action.Amounts) == 0 {
				continue
			}
			dataArgs := cw1155.GetParsedABI().Events["TransferBatch"].Inputs.NonIndexed()
			value, err := dataArgs.Pack(action.TokenIds, action.Amounts)
```

**File:** app/receipt.go (L436-441)
```go
		case "amounts":
			curAction.Amounts = utils.Map(strings.Split(value, ","), safeBigIntFromString)
		case "token_id":
			curAction.TokenId = safeBigIntFromString(value)
		case "token_ids":
			curAction.TokenIds = utils.Map(strings.Split(value, ","), safeBigIntFromString)
```

**File:** app/receipt.go (L461-467)
```go
func safeBigIntFromString(s string) *big.Int {
	sdkInt, ok := sdk.NewIntFromString(s)
	if !ok {
		return nil
	}
	return sdkInt.BigInt()
}
```
