This is exactly the analog. The `AddCosmosEventsToEVMReceiptIfApplicable` code in `app/receipt.go` explicitly has a code comment acknowledging the bug class ("txs with a very large number of WASM events may run out of gas") but the mitigation only bounds the *gas meter used inside the hook*, not the underlying operation's ability to complete — mirroring the CardAllocationPool pattern where a fixed/limited gas budget is applied to a loop whose iteration count is attacker/caller controlled (number of WASM events emitted, e.g. via ERC1155 `safeBatchTransferFrom` with many token IDs, or CW1155 `send_batch`).

### Title
Fixed/bounded `DeliverTxHookWasmGasLimit` causes synthetic EVM receipt/log generation to silently fail (out-of-gas) for transactions emitting many WASM events - ([File: app/receipt.go])

### Summary
`AddCosmosEventsToEVMReceiptIfApplicable` in `app/receipt.go` translates Cosmos/WASM events (CW20/CW721/CW1155 transfers routed through pointer contracts) into synthetic EVM logs so that EVM clients see a consistent receipt. This translation runs under a gas meter capped at `k.EvmKeeper.GetDeliverTxHookWasmGasLimit(ctx)` [1](#0-0) , a chain parameter with a small fixed default (`DefaultDeliverTxHookWasmGasLimit = 300000`) [2](#0-1) . The number of WASM events/logs that must be translated in the loop is driven entirely by user-submitted transaction content (e.g., a CW1155 `send_batch` or ERC1155 `safeBatchTransferFrom` with many token IDs) [3](#0-2) , with no cap on the number of events/batch size enforced before entering the translation loop [4](#0-3) .

### Finding Description
This is the same root-cause pattern as the reported CardAllocationPool bug: a fixed/underestimated gas budget is allocated to a loop whose length is controlled by an earlier, unbounded user action (there, the bundle size added via `addCardBundlesToPacketPool`; here, the number of CW/pointer transfer events produced by a single EVM/CW transaction). The code's own comment flags this exact risk: "Note: txs with a very large number of WASM events may run out of gas due to additional gas consumption from EVM receipt generation and event translation" [5](#0-4) . When the wrapped gas meter runs out during `translateCW20Event`/`translateCW721Event`/`translateCW1155Event`, the panic/out-of-gas condition is not scoped with its own explicit recovery in this function, unlike the deliverTxHook's other panics.

### Impact Explanation
If the synthetic-log translation panics from out-of-gas partway through the loop, the underlying Cosmos/WASM state transition (the actual token transfer) has already been committed by the time this deliver-tx hook runs (hooks fire "only... if DeliverTx is successful" [6](#0-5) ), but the EVM-side synthetic receipt/logs for that transaction would be incomplete or missing. This breaks EVM client/indexer visibility into completed asset transfers (pointer-based CW20/CW721/CW1155 transfers), which can mislead EVM-side integrations, DEXs, or bridges relying on receipt logs into believing a transfer did not happen, potentially causing double-processing or missed accounting — a fee/receipt integrity issue on the public JSON-RPC surface reachable by any transaction sender who triggers a large batch transfer.

### Likelihood Explanation
Likelihood is Medium: triggering this requires a single crafted transaction (e.g., a CW1155 batch send or ERC1155 `safeBatchTransferFrom` with many token IDs) routed through a registered pointer contract, which is directly reachable by any unprivileged EVM/CW transaction sender without needing special permissions.

### Recommendation
Either (1) cap the number of WASM/pointer events processed per transaction to a bound provably safe under `DeliverTxHookWasmGasLimit`, rejecting/truncating transactions that would exceed it, or (2) scale `DeliverTxHookWasmGasLimit` dynamically based on the number of events to translate (with a hard maximum), and (3) explicitly recover from out-of-gas panics inside `AddCosmosEventsToEVMReceiptIfApplicable` so a receipt-translation failure degrades gracefully (e.g., emits a shell/partial receipt with a clear error flag) rather than silently dropping logs.

### Proof of Concept
1. Register an ERC1155/CW1155 pointer contract via the module's pointer registration flow.
2. Submit a single transaction calling `CW1155ERC1155Pointer.safeBatchTransferFrom` (or the equivalent CW `send_batch` message) with an ids/amounts array long enough that the number of WASM events emitted, when translated via `translateCW1155Event`, exceeds what `DeliverTxHookWasmGasLimit` (300,000 gas by default) can process [7](#0-6) .
3. Observe that `AddCosmosEventsToEVMReceiptIfApplicable` runs out of gas mid-loop while translating events [8](#0-7) , producing an incomplete/missing set of synthetic EVM logs for a transaction whose underlying CW state transition already succeeded.

### Citations

**File:** app/receipt.go (L42-43)
```go
func (app *App) AddCosmosEventsToEVMReceiptIfApplicable(ctx sdk.Context, tx sdk.Tx, checksum [32]byte, response sdk.DeliverTxHookInput) {
	// hooks will only be called if DeliverTx is successful
```

**File:** app/receipt.go (L49-104)
```go
	// Note: txs with a very large number of WASM events may run out of gas due to
	// additional gas consumption from EVM receipt generation and event translation
	wasmToEvmEventGasLimit := app.EvmKeeper.GetDeliverTxHookWasmGasLimit(ctx.WithGasMeter(sdk.NewInfiniteGasMeter(1, 1)))
	wasmToEvmEventCtx := ctx.WithGasMeter(sdk.NewGasMeterWithMultiplier(ctx, wasmToEvmEventGasLimit))
	// unfortunately CW721 transfer events differ from ERC721 transfer events
	// in that CW721 include sender (which can be different than owner) whereas
	// ERC721 always include owner. The following logic refer to the owner
	// event emitted before the transfer and use that instead to populate the
	// synthetic ERC721 event.
	ownerEvents := GetEventsOfType(response, wasmtypes.EventTypeCW721PreTransferOwner)
	ownerEventsMap := map[string][]abci.Event{}
	for _, ownerEvent := range ownerEvents {
		if len(ownerEvent.Attributes) != 3 {
			logger.Error("received owner event with number of attributes != 3")
			continue
		}
		ownerEventKey := getOwnerEventKey(string(ownerEvent.Attributes[0].Value), string(ownerEvent.Attributes[1].Value))
		if events, ok := ownerEventsMap[ownerEventKey]; ok {
			ownerEventsMap[ownerEventKey] = append(events, ownerEvent)
		} else {
			ownerEventsMap[ownerEventKey] = []abci.Event{ownerEvent}
		}
	}
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

**File:** x/evm/types/params.go (L32-34)
```go
var DefaultBaseFeePerGas = sdk.NewDec(0)         // used for static base fee, deprecated in favor of dynamic base fee
var DefaultMinFeePerGas = sdk.NewDec(1000000000) // 1gwei
var DefaultDeliverTxHookWasmGasLimit = uint64(300000)
```

**File:** contracts/src/CW1155ERC1155Pointer.sol (L78-120)
```text
    function safeBatchTransferFrom(
        address from,
        address to,
        uint256[] memory ids,
        uint256[] memory amounts,
        bytes memory data
    ) public override {
        require(to != address(0), "ERC1155: transfer to the zero address");
        require(
            msg.sender == from || isApprovedForAll(from, msg.sender),
            "ERC1155: caller is not approved to transfer"
        );
        require(ids.length == amounts.length, "ERC1155: ids and amounts length mismatch");
        address[] memory batchFrom = new address[](ids.length);
        for (uint256 i = 0; i < ids.length; i++) {
            batchFrom[i] = from;
        }
        uint256[] memory balances = balanceOfBatch(batchFrom, ids);
        for (uint256 i = 0; i < balances.length; i++) {
            require(balances[i] >= amounts[i], "ERC1155: insufficient balance for transfer");
        }

        string memory payload = string.concat("{\"send_batch\":{\"from\":\"", AddrPrecompile.getSeiAddr(from));
        payload = string.concat(payload, "\",\"to\":\"");
        payload = string.concat(payload, AddrPrecompile.getSeiAddr(to));
        payload = string.concat(payload, "\",\"batch\":[");
        for (uint256 i = 0; i < ids.length; i++) {
            string memory batch = string.concat("{\"token_id\":\"", Strings.toString(ids[i]));
            batch = string.concat(batch, "\",\"amount\":\"");
            batch = string.concat(batch, Strings.toString(amounts[i]));
            if (i < ids.length - 1) {
                batch = string.concat(batch, "\"},");
            } else {
                batch = string.concat(batch, "\"}");
            }
            payload = string.concat(payload, batch);
        }
        payload = string.concat(payload, "]}}");
        _execute(bytes(payload));
        if (to.code.length > 0) {
            require(
                IERC1155Receiver(to).onERC1155BatchReceived(
                    msg.sender,
```
