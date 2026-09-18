## Analog Found

### Title
CW721↔ERC721 pointer Transfer/Approval events depend on an unguarded, gas-metered synthetic-log translation path that can silently drop or malform required EIP-721 events - (File: app/receipt.go)

### Summary
The original Kairos report flags that `safeMint` intentionally no-ops `emitTransfer`, so EIP-721-mandated `Transfer` events never reach off-chain monitors. In sei-chain, `CW721ERC721Pointer.sol` has the analogous structural issue: its `transferFrom`, `approve`, and `setApprovalForAll` functions never call Solidity `emit Transfer`/`emit Approval` themselves [1](#0-0) . Instead, all ERC721 events for CW721 pointer contracts are synthesized after the fact by `translateCW721Event`, driven off raw CosmWasm wasm-module events [2](#0-1) , and injected into the transient EVM receipt inside `AddCosmosEventsToEVMReceiptIfApplicable` [3](#0-2) .

### Finding Description
This event-translation path runs under a dedicated, capped gas meter (`wasmToEvmEventGasLimit` / `wasmToEvmEventCtx`), with an explicit code comment warning that "txs with a very large number of WASM events may run out of gas due to additional gas consumption from EVM receipt generation and event translation" [4](#0-3) .

For CW20, the author defensively wrapped the equivalent translator in a `defer/recover` to swallow any panic (e.g. out-of-gas) from that metered context [5](#0-4) . `translateCW721Event`, which performs comparable metered work (looking up owner events, calling `GetEVMAddressOrDefault`, iterating maps) under the very same shared gas-limited `wasmToEvmEventCtx`, has no such guard [6](#0-5) . `translateCW1155Event` similarly lacks a recover wrapper.

Additionally, even in the non-panicking case, the CW721 "transfer_nft"/"send_nft"/"burn" branch degrades gracefully but incorrectly when the correlated `EventTypeCW721PreTransferOwner` event is missing or exhausted: it logs an error and falls back to `action.Sender` (rather than the true owner) as the synthesized `Transfer.from`, or leaves it at `EmptyHash` in edge cases [7](#0-6) , producing a Transfer event whose `from` field doesn't match EIP-721 semantics.

### Impact Explanation
Because the pointer contract itself never emits `Transfer`/`Approval` directly in Solidity, any failure in this asynchronous, gas-metered, best-effort reconstruction — whether from gas exhaustion (unguarded panic in `translateCW721Event`/`translateCW1155Event`) or from missing/insufficient owner-correlation events — results in the same class of problem the original report describes: EIP-721/1155 compliant `Transfer`/`TransferSingle` events are not reliably emitted for a mint/transfer/approve action that indisputably occurred on-chain via the pointer. Indexers, wallets, and marketplaces that rely on `Transfer` events to track NFT ownership through `CW721ERC721Pointer` (and CW1155 pointer) will silently miss or misattribute state changes. This is a functional/compliance defect consistent with a Medium-severity analog rather than direct fund loss.

### Likelihood Explanation
This path is reachable by any unprivileged user simply calling `transferFrom`/`approve`/`setApprovalForAll` on a `CW721ERC721Pointer` contract, or executing high-volume/batch CW721 operations against a pointer-registered CW721 contract that pushes wasm-event translation gas usage close to the dedicated `wasmToEvmEventGasLimit`. No special privileges, malicious peers, or governance actions are needed — the trigger condition is a normal, permissionless transaction pattern (e.g., a contract that emits many wasm sub-events per execute call, or bulk pointer operations in one tx).

### Recommendation
Wrap `translateCW721Event` and `translateCW1155Event` with the same `defer/recover` pattern already used in `translateCW20Event` so a metered-gas panic degrades to "no synthetic events" instead of an unhandled panic in the DeliverTx hook. Separately, consider emitting `Transfer`/`Approval` directly from `CW721ERC721Pointer.sol` state transitions (mirroring standard ERC721 `_mint`/`_burn`/`_transfer` patterns already used in `contracts/src/ERC721.sol`) rather than relying entirely on post-hoc reconstruction from wasm events, and treat a missing/insufficient owner-correlation event as a hard failure (revert or omit the log) rather than falling back to a potentially incorrect `sender` address.

### Proof of Concept
1. Register a CW721 contract as an ERC721 pointer via `SetERC721CW721Pointer`.
2. Submit an EVM tx that calls `CW721ERC721Pointer.transferFrom` (or directly execute a CW721 message on the underlying contract) in a way that produces many wasm sub-events in a single wasm-module event batch, driving gas consumption in `wasmToEvmEventCtx` above `GetDeliverTxHookWasmGasLimit`.
3. Observe that `translateCW721Event` panics on out-of-gas without recovery [8](#0-7) , unlike the CW20 path which recovers cleanly [5](#0-4) , so the resulting EVM transaction receipt for the mint/transfer contains no `Transfer` log — an EIP-721 compliance violation identical in effect to the referenced Kairos finding.

### Citations

**File:** contracts/src/CW721ERC721Pointer.sol (L160-176)
```text
    function transferFrom(address from, address to, uint256 tokenId) public override {
        if (to == address(0)) {
            revert ERC721InvalidReceiver(address(0));
        }
        require(from == ownerOf(tokenId), "`from` must be the owner");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("transfer_nft", _curlyBrace(_join(recipient, tId, ","))));
        _execute(bytes(req));
    }

    function approve(address approved, uint256 tokenId) public override {
        string memory spender = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(approved)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("approve", _curlyBrace(_join(spender, tId, ","))));
        _execute(bytes(req));
    }
```

**File:** app/receipt.go (L42-47)
```go
func (app *App) AddCosmosEventsToEVMReceiptIfApplicable(ctx sdk.Context, tx sdk.Tx, checksum [32]byte, response sdk.DeliverTxHookInput) {
	// hooks will only be called if DeliverTx is successful
	wasmEvents := GetEventsOfType(response, wasmtypes.WasmModuleEventType)
	if len(wasmEvents) == 0 {
		return
	}
```

**File:** app/receipt.go (L48-52)
```go
	logs := []*ethtypes.Log{}
	// Note: txs with a very large number of WASM events may run out of gas due to
	// additional gas consumption from EVM receipt generation and event translation
	wasmToEvmEventGasLimit := app.EvmKeeper.GetDeliverTxHookWasmGasLimit(ctx.WithGasMeter(sdk.NewInfiniteGasMeter(1, 1)))
	wasmToEvmEventCtx := ctx.WithGasMeter(sdk.NewGasMeterWithMultiplier(ctx, wasmToEvmEventGasLimit))
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

**File:** app/receipt.go (L200-253)
```go
func (app *App) translateCW721Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string,
	ownerEventsMap map[string][]abci.Event, cw721TransferCounterMap map[string]int) (res []*ethtypes.Log) {
	for _, action := range app.GetActionsFromWasmEvent(ctx, wasmEvent) {
		switch action.Type {
		case "transfer_nft", "send_nft", "burn":
			if action.TokenId == nil {
				continue
			}
			sender := action.Sender
			ownerEventKey := getOwnerEventKey(contractAddr, action.TokenId.String())
			var currentCounter int
			if c, ok := cw721TransferCounterMap[ownerEventKey]; ok {
				currentCounter = c
			}
			cw721TransferCounterMap[ownerEventKey] = currentCounter + 1
			if ownerEvents, ok := ownerEventsMap[ownerEventKey]; ok {
				if len(ownerEvents) > currentCounter {
					ownerSeiAddrStr := string(ownerEvents[currentCounter].Attributes[2].Value)
					if ownerSeiAddr, err := sdk.AccAddressFromBech32(ownerSeiAddrStr); err == nil {
						ownerEvmAddr := app.EvmKeeper.GetEVMAddressOrDefault(ctx, ownerSeiAddr)
						sender = common.BytesToHash(ownerEvmAddr[:])
					} else {
						logger.Error("Translate CW721 error: invalid bech32 owner", "address", ownerSeiAddrStr, "err", err)
					}
				} else {
					logger.Error("Translate CW721 error: insufficient owner events", "key", ownerEventKey, "counter", currentCounter, "events", len(ownerEvents))
				}
			} else {
				logger.Error("Translate CW721 error: owner event not found", "key", ownerEventKey)
			}
			res = append(res, &ethtypes.Log{
				Address: pointerAddr,
				Topics: []common.Hash{
					ERC721TransferTopic,
					sender,
					action.Recipient,
					common.BigToHash(action.TokenId),
				},
				Data: EmptyHash.Bytes(),
			})
		case "mint":
			if action.TokenId == nil {
				continue
			}
			res = append(res, &ethtypes.Log{
				Address: pointerAddr,
				Topics: []common.Hash{
					ERC721TransferTopic,
					EmptyHash,
					action.Owner,
					common.BigToHash(action.TokenId),
				},
				Data: EmptyHash.Bytes(),
			})
```
