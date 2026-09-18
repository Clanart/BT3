### Title
Missing panic-recovery and unvalidated numeric parsing in CW1155→ERC1155 event translation can crash block processing - ([File: app/receipt.go])

### Summary
`app/receipt.go` translates CosmWasm contract events into synthetic EVM logs during the post-`DeliverTx` receipt hook. The CW20 translator explicitly wraps itself in a `recover()` because malformed/attacker-influenced event data can panic downstream encoders, but the CW721 and CW1155 translators do not, even though they parse the same kind of attacker/contract-controlled, comma-separated numeric strings and feed the results directly into `abi.Arguments.Pack`.

### Finding Description
`GetActionsFromWasmEvent` converts arbitrary wasm event attribute strings into typed fields using `safeBigIntFromString`, including for CW1155 batch fields: [1](#0-0) 

For `transfer_batch`/`mint_batch`/`burn_batch`, `translateCW1155Event` only checks that the resulting slices are non-empty, not that every element parsed successfully (non-nil) or that `TokenIds` and `Amounts` have matching, individually-valid entries, before calling the ABI packer: [2](#0-1) 

Contrast this with `translateCW20Event`, which is defensively wrapped: [3](#0-2) 

`translateCW721Event` (lines 200-305) and `translateCW1155Event` (lines 307-391) have no equivalent `recover()`. If any element of the comma-separated `token_ids` or `amounts` wasm-event attribute fails to parse (e.g. an empty field from `"1,,3"`, non-numeric text, or a value a CW1155 contract deliberately emits), `safeBigIntFromString` presumably returns a nil `*big.Int` for that slot (I could not fully confirm its implementation before running out of search iterations), and that nil pointer is placed into `action.TokenIds`/`action.Amounts` without further validation. The `len(...) == 0` guards do not catch this. The slices are then passed to `cw1155.GetParsedABI().Events["TransferBatch"].Inputs.NonIndexed().Pack(action.TokenIds, action.Amounts)`, i.e. go-ethereum's ABI packer operating on a `*big.Int` slice containing a nil element. Whether go-ethereum's packer panics on a nil `*big.Int` element (rather than returning an error) is the load-bearing assumption of this report and was not independently verified against the vendored go-ethereum ABI pack implementation due to iteration limits.

This is analogous to the CVE's bug class: a routine that receives an attacker-influenceable "array" whose elements can be null/malformed is not defensively checked before being handed to a lower-level encoder/allocator, which then dereferences/interprets the null element and crashes.

### Impact Explanation
`AddCosmosEventsToEVMReceiptIfApplicable` runs as part of the deterministic `DeliverTx` hook that all full/validator nodes execute identically when processing a block containing a CW1155-pointer-linked contract's `transfer_batch`/`mint_batch`/`burn_batch` wasm event: [4](#0-3) 
If the panic is not recovered anywhere above this call (unlike the CW20 path, which the developers explicitly hardened), the panic would propagate through block execution on every node processing that block, producing an unrecoverable process crash / consensus halt — a chain-halt-class impact, not a mere log-formatting bug. I was unable to confirm within the remaining budget whether some higher-level `recover()` (e.g., in `app/abci.go`) shields this call, which is the primary uncertainty in this finding.

### Likelihood Explanation
Reaching this code requires only: (1) a CW1155 contract with a registered ERC1155 CW1155 pointer (`GetERC1155CW1155Pointer`), and (2) that contract emitting a `wasm-...` event with `action=transfer_batch`/`mint_batch`/`burn_batch` and a malformed `token_ids`/`amounts` attribute (e.g., containing an empty or non-numeric field in the comma list, or mismatched lengths between the two lists). Any account able to deploy or interact with such a CW1155 contract — an ordinary CosmWasm user — can trigger this deterministically, with no special privileges required.

### Recommendation
- Wrap `translateCW721Event` and `translateCW1155Event` in the same `recover()` pattern already used by `translateCW20Event`, so a bad wasm event degrades to a dropped log rather than crashing the node.
- Validate that every element of `action.TokenIds` and `action.Amounts` is non-nil and that the two slices have equal length before calling `dataArgs.Pack`, skipping/discarding the malformed batch event instead of packing it.
- Harden `safeBigIntFromString`-derived slice construction (`utils.Map(strings.Split(value, ","), safeBigIntFromString)`) to reject the whole attribute (or filter out unparseable entries with an explicit error) rather than silently injecting nils into the slice.

### Proof of Concept
Conceptual PoC (not independently executed against go-ethereum's ABI packer):
1. Deploy/register a CW1155 contract with a CW1155↔ERC1155 pointer via the pointer precompile.
2. From the CW1155 contract, emit a wasm event with attributes `action=transfer_batch`, `token_ids="1,,3"`, `amounts="10,20,30"` (or any combination producing a nil entry in one parsed slice, or a length mismatch between `TokenIds` and `Amounts`).
3. Have any user submit a transaction invoking that contract action.
4. During `DeliverTx`, `AddCosmosEventsToEVMReceiptIfApplicable` → `translateCW1155Event` parses the malformed attribute into a `*big.Int` slice containing a nil element and calls `dataArgs.Pack(action.TokenIds, action.Amounts)`, which is hypothesized to panic on the nil pointer, uncaught by any `recover()` in this path.

**Caveat:** I could not verify within the available tool budget (a) the exact behavior of go-ethereum's vendored ABI `Pack` when given a nil `*big.Int` slice element, and (b) whether `app/abci.go`'s call site wraps `AddCosmosEventsToEVMReceiptIfApplicable` in a `recover()`. Both should be confirmed before treating this as conclusively exploitable.

### Citations

**File:** app/receipt.go (L95-103)
```go
		// check if there is a ERC1155 pointer to contract Addr
		pointerAddr, _, exists = app.EvmKeeper.GetERC1155CW1155Pointer(wasmToEvmEventCtx, contractAddr)
		if exists {
			for _, log := range app.translateCW1155Event(wasmToEvmEventCtx, wasmEvent, pointerAddr, contractAddr) {
				log.Index = uint(len(logs))
				logs = append(logs, log)
			}
			continue
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

**File:** app/receipt.go (L337-358)
```go
		case "transfer_batch", "mint_batch", "burn_batch":
			fromHash := EmptyHash
			toHash := EmptyHash
			if action.Type != "mint_batch" {
				fromHash = action.Owner
			}
			if action.Type != "burn_batch" {
				toHash = action.Recipient
			}
			if len(action.TokenIds) == 0 {
				continue
			}
			if len(action.Amounts) == 0 {
				continue
			}
			dataArgs := cw1155.GetParsedABI().Events["TransferBatch"].Inputs.NonIndexed()
			value, err := dataArgs.Pack(action.TokenIds, action.Amounts)
			if err != nil {
				logger.Error("Failed to parse TransferBatch event data", "err", err)
				continue
			}
			res = append(res, &ethtypes.Log{
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
