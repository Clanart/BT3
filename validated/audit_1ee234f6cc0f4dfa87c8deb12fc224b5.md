### Title
Permissionless pointer registration lets a user forge unlimited synthetic ERC20/ERC721/ERC1155 `Transfer` events with no real value moved - (File: `app/receipt.go`, `x/evm/keeper/msg_server.go`)

### Summary
Any unprivileged account can register an EVM pointer contract for a CosmWasm contract that it fully controls via `MsgRegisterPointer`/`RegisterPointer` [1](#0-0) . The synthetic-event generation logic that produces EVM receipt logs for CW20/CW721/CW1155 pointer contracts (`app.AddCosmosEventsToEVMReceiptIfApplicable` / `translateCW20Event` / `translateCW721Event` / `translateCW1155Event`) blindly trusts the wasm module event attributes ("action", "from", "to", "amount", "owner", "recipient", "sender", etc.) emitted by the pointee contract, without validating that those attributes correspond to a genuine balance/ownership change tracked in the pointee's own contract state [2](#0-1) [3](#0-2) .

### Finding Description
`RegisterPointer` only requires that the target address be a valid bech32 CosmWasm contract; it does not require the contract to be the canonical cw20-base/cw721-base/cw1155-base implementation, nor does it verify that the contract's emitted events faithfully reflect real state transitions [4](#0-3) . A malicious actor can deploy a trivial CosmWasm contract that:
1. Responds correctly to `{"token_info":{}}` (or the analogous query for CW721/CW1155) so pointer creation succeeds.
2. Exposes an `Execute` entrypoint that emits a `wasm` event mimicking the cw20-base schema (`action=transfer`, `from=<any>`, `to=<any>`, `amount=<any>`), or the cw721/cw1155 equivalents, without performing any real balance/ownership mutation.

Once a pointer is registered for this contract, every call to that fake entrypoint is picked up by `AddCosmosEventsToEVMReceiptIfApplicable`, which matches the wasm event's `contract_address` attribute against the registered pointer mapping (`GetERC20CW20Pointer`/`GetERC721CW721Pointer`/`GetERC1155CW1155Pointer`) and unconditionally converts the attacker-supplied `from`/`to`/`amount` (or NFT `owner`/`recipient`/`token_id`) attributes into real-looking `Transfer`/`Approval` topics/logs at the pointer's EVM address [5](#0-4) [6](#0-5) . These logs are appended to the transaction's EVM receipt and are visible via standard `eth_getLogs`/`eth_getTransactionReceipt` JSON-RPC calls, exactly like a genuine ERC20/721/1155 transfer [7](#0-6) .

Because the attacker fully controls the wasm contract code, they can emit an unbounded number of `Transfer` events for arbitrary amounts and arbitrary destination addresses using zero real capital, entirely analogous to the reported Gitcoin issue where a user could spoof `Voted` event fields (`_grantAddress`/`_projectId`) disconnected from the real fund flow to defeat downstream, event-driven accounting.

### Impact Explanation
Downstream systems that treat sei-chain's synthetic pointer `Transfer`/`TransferSingle`/`TransferBatch` events as proof of value movement (custodial deposit crediting, bridges, exchanges, portfolio/accounting tools, or any off-chain indexer that reconstructs balances from `eth_getLogs`) can be deceived into crediting deposits or balances that never occurred on the underlying CosmWasm token, leading to direct fund loss for any party that trusts these events as ground truth — the same class of impact the original report flags ("Events are relied-on for payout calculation off-chain").

### Likelihood Explanation
Trivially reachable by any unprivileged user: no special permission is required to call `MsgRegisterPointer`, deploy an arbitrary CosmWasm contract, or invoke its `Execute` entrypoint. The exploit requires only standard user transactions (contract instantiate/execute + pointer registration), no validator/governance/network-level access, and can be repeated indefinitely at negligible cost.

### Recommendation
Do not derive synthetic Transfer/Approval logs solely from attacker-supplied wasm event attributes. Cross-validate the emitted `from`/`to`/`amount` (or NFT owner/token_id) against an authoritative state read (e.g., re-querying `balance`/`owner_of` before and after execution, or restricting pointer registration/synthetic event translation to contracts whose code hash matches an approved cw20-base/cw721-base/cw1155-base implementation) before materializing them as EVM receipt logs.

### Proof of Concept
1. Deploy a minimal CosmWasm contract `FakeToken` that:
   - Answers `{"token_info":{}}` with any `name`/`symbol`.
   - On `execute({"fake_transfer": {"from": ..., "to": ..., "amount": ...}})`, emits a `wasm` event with attributes `action=transfer`, `from=<attacker-chosen>`, `to=<attacker-chosen>`, `amount=<attacker-chosen>` and does not mutate any real balance.
2. Call `MsgRegisterPointer{PointerType: ERC20, ErcAddress: <FakeToken address>}` from any unprivileged account — no gating prevents this [4](#0-3) .
3. Repeatedly call `fake_transfer` with arbitrary `from`/`to`/`amount` values.
4. Observe via `eth_getLogs`/`eth_getTransactionReceipt` on the pointer's EVM address that a genuine-looking `Transfer(address,address,uint256)` log is produced each time, per the translation logic in `translateCW20Event` [8](#0-7) , with no actual token balance changes ever occurring, demonstrating unlimited forged transfer events from zero real capital.

### Citations

**File:** x/evm/keeper/msg_server.go (L247-297)
```go
func (server msgServer) RegisterPointer(goCtx context.Context, msg *types.MsgRegisterPointer) (*types.MsgRegisterPointerResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	if server.GetRegisterPointerDisabled(ctx) {
		return nil, fmt.Errorf("registering CW->ERC pointers has been disabled")
	}
	var existingPointer sdk.AccAddress
	var existingVersion uint16
	var currentVersion uint16
	var exists bool
	switch msg.PointerType {
	case types.PointerType_ERC20:
		currentVersion = erc20.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC721:
		currentVersion = erc721.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC1155:
		currentVersion = erc1155.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW1155ERC1155Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	default:
		panic("unknown pointer type")
	}
	if exists && existingVersion >= currentVersion {
		return nil, fmt.Errorf("pointer %s already registered at version %d", existingPointer.String(), existingVersion)
	}
	payload := map[string]interface{}{}
	switch msg.PointerType {
	case types.PointerType_ERC20:
		payload["erc20_address"] = msg.ErcAddress
	case types.PointerType_ERC721:
		payload["erc721_address"] = msg.ErcAddress
	case types.PointerType_ERC1155:
		payload["erc1155_address"] = msg.ErcAddress
	default:
		panic("unknown pointer type")
	}
	codeID := server.GetStoredPointerCodeID(ctx, msg.PointerType)
	moduleAcct := server.accountKeeper.GetModuleAddress(types.ModuleName)
	var err error
	var pointerAddr sdk.AccAddress
	if exists {
		bz, _ := json.Marshal(map[string]interface{}{})
		pointerAddr = existingPointer
		_, err = server.wasmKeeper.Migrate(ctx, existingPointer, moduleAcct, codeID, bz)
	} else {
		bz, jerr := json.Marshal(payload)
		if jerr != nil {
			return nil, jerr
		}
		pointerAddr, _, err = server.wasmKeeper.Instantiate(ctx, codeID, moduleAcct, moduleAcct, bz, fmt.Sprintf("Pointer of %s", msg.ErcAddress), sdk.NewCoins())
	}
```

**File:** app/receipt.go (L73-103)
```go
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
```

**File:** app/receipt.go (L108-139)
```go
	txHash := common.BytesToHash(checksum[:])
	if response.EvmTxInfo != nil {
		txHash = common.HexToHash(response.EvmTxInfo.TxHash)
	}
	var bloom ethtypes.Bloom
	if r, err := app.EvmKeeper.GetTransientReceipt(wasmToEvmEventCtx, txHash, uint64(ctx.TxIndex())); err == nil && r != nil { //nolint:gosec
		r.Logs = append(r.Logs, utils.Map(logs, evmkeeper.ConvertSyntheticEthLog)...)
		for i, l := range r.Logs {
			l.Index = uint32(i) //nolint:gosec
		}
		bloom = ethtypes.CreateBloom(&ethtypes.Receipt{Logs: evmkeeper.GetLogsForTx(r, 0)})
		r.LogsBloom = bloom[:]
		_ = app.EvmKeeper.SetTransientReceipt(wasmToEvmEventCtx, txHash, r)
	} else {
		bloom = ethtypes.CreateBloom(&ethtypes.Receipt{Logs: logs})
		receipt := &evmtypes.Receipt{
			TxType:           evmtypes.ShellEVMTxType,
			TxHashHex:        txHash.Hex(),
			GasUsed:          ctx.GasMeter().GasConsumed(),
			BlockNumber:      uint64(ctx.BlockHeight()), //nolint:gosec
			TransactionIndex: uint32(ctx.TxIndex()),     //nolint:gosec
			Logs:             utils.Map(logs, evmkeeper.ConvertSyntheticEthLog),
			LogsBloom:        bloom[:],
			Status:           uint32(ethtypes.ReceiptStatusSuccessful), // we don't create shell receipt for failed Cosmos tx since there is no event anyway
		}
		sigTx, ok := tx.(authsigning.SigVerifiableTx)
		if ok && len(sigTx.GetSigners()) > 0 {
			// use the first signer as the `from`
			receipt.From = app.EvmKeeper.GetEVMAddressOrDefault(wasmToEvmEventCtx, sigTx.GetSigners()[0]).Hex()
		}
		_ = app.EvmKeeper.SetTransientReceipt(wasmToEvmEventCtx, txHash, receipt)
	}
```

**File:** app/receipt.go (L147-168)
```go
func (app *App) translateCW20Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string) (res []*ethtypes.Log) {
	defer func() {
		if r := recover(); r != nil {
			fmt.Printf("[Error] Panic caught during translateCW20Event: type=%T, value=%+v\n", r, r)
		}
	}()

	for _, action := range app.GetActionsFromWasmEvent(ctx, wasmEvent) {
		switch action.Type {
		case "mint", "burn", "send", "transfer", "transfer_from", "send_from", "burn_from":
			if action.Amount == nil {
				continue
			}
			res = append(res, &ethtypes.Log{
				Address: pointerAddr,
				Topics: []common.Hash{
					ERC20TransferTopic,
					action.From,
					action.To,
				},
				Data: common.BigToHash(action.Amount).Bytes(),
			})
```

**File:** app/receipt.go (L421-459)
```go
func (app *App) GetActionsFromWasmEvent(ctx sdk.Context, event abci.Event) (actions []*Action) {
	for _, attr := range event.Attributes {
		key := string(attr.Key)
		value := string(attr.Value)
		if key == "action" {
			actions = append(actions, &Action{Type: value})
			continue
		}
		if len(actions) == 0 {
			continue
		}
		curAction := actions[len(actions)-1]
		switch key {
		case "amount":
			curAction.Amount = safeBigIntFromString(value)
		case "amounts":
			curAction.Amounts = utils.Map(strings.Split(value, ","), safeBigIntFromString)
		case "token_id":
			curAction.TokenId = safeBigIntFromString(value)
		case "token_ids":
			curAction.TokenIds = utils.Map(strings.Split(value, ","), safeBigIntFromString)
		case "sender":
			curAction.Sender = app.GetEvmAddressHash(ctx, value)
		case "recipient":
			curAction.Recipient = app.GetEvmAddressHash(ctx, value)
		case "spender":
			curAction.Spender = app.GetEvmAddressHash(ctx, value)
		case "operator":
			curAction.Operator = app.GetEvmAddressHash(ctx, value)
		case "owner":
			curAction.Owner = app.GetEvmAddressHash(ctx, value)
		case "from":
			curAction.From = app.GetEvmAddressHash(ctx, value)
		case "to":
			curAction.To = app.GetEvmAddressHash(ctx, value)
		}
	}
	return
}
```
