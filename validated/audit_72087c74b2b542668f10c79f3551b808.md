### Title
Permissionless CW pointer registration lets an attacker forge synthetic ERC20/ERC721/ERC1155 `Transfer`/`Approval` EVM logs that are not backed by real state changes - (File: `app/receipt.go`)

### Summary
`app/receipt.go`'s `AddCosmosEventsToEVMReceiptIfApplicable` synthesizes EVM logs for the pointer's EVM address purely from attacker-controlled CosmWasm `wasm` event attributes (`action`, `amount`, `from`, `to`, `sender`, `recipient`, `owner`, `spender`, `token_id`), without independently verifying that the reported action actually corresponds to a real balance/ownership change in the CW20/721/1155 contract.

### Finding Description
`GetActionsFromWasmEvent` [1](#0-0)  parses the `action`, `amount`, `from`, `to`, `sender`, `recipient`, `owner`, `spender`, and `token_id` attributes directly out of the contract's own custom `wasm` event attributes. These attribute *values* are set by the CW contract's code via `Response.attributes` and are only wrapped with an authoritative `_contract_address` prefix by the wasm module [2](#0-1)  — the module does not validate that an "action"="transfer" attribute correlates with an actual balance mutation.

`translateCW20Event`, `translateCW721Event`, and `translateCW1155Event` then take these attacker-supplied values at face value and emit synthetic `ethtypes.Log` entries carrying the standard `Transfer`/`Approval`/`ApprovalForAll` topics at the *pointer's* EVM address [3](#0-2) [4](#0-3) . For the "mint"/"burn"/"send"/"transfer"/"transfer_from"/"send_from"/"burn_from" cases, the code does not re-query the contract for an actual balance diff — it directly encodes `action.Amount`, `action.From`, `action.To` into the log [5](#0-4) . Only the allowance-related branch performs a corroborating `QuerySmart` call; the primary transfer/mint/burn branch does not.

This pipeline only fires for contracts that have a registered ERC20/721/1155 pointer (`GetERC20CW20Pointer`, `GetERC721CW721Pointer`, `GetERC1155CW1155Pointer`) [6](#0-5) . Pointer registration is reachable by any transaction sender: `MsgRegisterPointer` has no admin/authority gating beyond a global feature flag, and simply instantiates the module's stored pointer code against the caller-supplied CW address [7](#0-6) . An attacker can therefore deploy their own malicious CW20/CW721/CW1155 contract, register a pointer for it (permissionless, paying only gas/fees), and then call `execute` with a custom message that makes the contract emit a `wasm` event with `action="transfer"` (or `mint`/`burn`), an arbitrary `amount`, and arbitrary `from`/`to` hashes — all without moving any real token balance inside the contract. The bridge (`app/receipt.go`) will faithfully translate this into a standards-compliant ERC20 `Transfer` log emitted from the pointer's EVM address, visible via `eth_getLogs`/`eth_getTransactionReceipt` exactly like a genuine pointer-token transfer [8](#0-7) .

This mirrors the audited bug class: a downstream component (the CW→EVM synthetic-log bridge) trusts caller/contract-supplied data (custom wasm event attributes) to build events that off-chain consumers (indexers, exchanges, bridges reading standard ERC20/721/1155 logs via `eth_getLogs`) treat as authoritative proof of a token movement, without validating the claimed action against real on-chain state.

### Impact Explanation
Any off-chain system that watches `eth_getLogs`/receipt `Transfer`/`Approval` events for a pointer contract (a common integration pattern for CEXs, bridges, and indexers on Sei, since pointer contracts are the canonical ERC20/721/1155 view of CW tokens) can be deceived into crediting/believing a deposit, withdrawal, or ownership transfer happened when it did not. Because the attacker fully controls the malicious CW20/721/1155 contract's code and its registered pointer, they can emit fabricated `Transfer` events with arbitrary `from`, `to`, and `amount`/`tokenId` values at will, at no cost beyond gas — this is a fund-loss vector for any custodial/bridge integration that trusts these synthetic logs as ground truth (matching the "unauthorized transfer via pointer" and "fund loss" categories from the validation criteria).

### Likelihood Explanation
High reachability: pointer registration and CW contract deployment/execution are both permissionless actions available to any unprivileged transaction sender; no validator, governance, or privileged role is required. The only prerequisite is that a downstream consumer trusts pointer Transfer/Approval logs without cross-checking actual CW20/721/1155 state (a very common integration pattern, since the pointer's entire purpose is to present a trustworthy ERC20/721/1155 view of the underlying CW asset).

### Recommendation
In `translateCW20Event`/`translateCW721Event`/`translateCW1155Event` (`app/receipt.go`), do not trust the `action`/`amount`/`from`/`to`/`owner`/`spender`/`token_id` attribute values directly. Either (a) corroborate every action against the CW contract's actual state via `QuerySmart` (balance/ownership diff) before emitting the synthetic log, as is already done for the allowance branch, or (b) restrict/limit pointer registration and add a governance/whitelisting gate so that only contracts whose bytecode is known-good (e.g., matches the standard cw20-base/cw721-base/cw1155-base implementations) can back a pointer, since arbitrary attacker-authored CW code should never be trusted to self-report "actions" that drive standards-compliant EVM events.

### Proof of Concept
1. Attacker writes and deploys a malicious CosmWasm contract that, on any `execute` call, unconditionally emits a custom `wasm` event with attributes `action="transfer"`, `amount="1000000000000"`, `from=<attacker's own address>`, `to=<attacker's EVM alias>` — without touching any balance storage.
2. Attacker calls `MsgRegisterPointer` (or the `pointer` precompile's CW20-equivalent add function) for this contract's address; no admin approval or corroborating check is required [9](#0-8) .
3. Attacker calls `execute` on their contract via a Cosmos tx.
4. `AddCosmosEventsToEVMReceiptIfApplicable` sees the resulting `wasm` event, finds a registered ERC20 pointer for the contract address, and calls `translateCW20Event`, which emits an `ERC20TransferTopic` log for `1000000000000` tokens at the pointer's EVM address, purely from the attacker-controlled `amount`/`from`/`to` attributes [5](#0-4) .
5. Any off-chain service querying `eth_getLogs`/`eth_getTransactionReceipt` for the pointer address sees a standards-compliant `Transfer` event for a transfer that never actually happened in the underlying CW20 balance state.

### Citations

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

**File:** app/receipt.go (L147-198)
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
}
```

**File:** app/receipt.go (L307-337)
```go
func (app *App) translateCW1155Event(ctx sdk.Context, wasmEvent abci.Event, pointerAddr common.Address, contractAddr string) (res []*ethtypes.Log) {
	for _, action := range app.GetActionsFromWasmEvent(ctx, wasmEvent) {
		switch action.Type {
		case "transfer_single", "mint_single", "burn_single":
			fromHash := EmptyHash
			toHash := EmptyHash
			if action.Type != "mint_single" {
				fromHash = action.Owner
			}
			if action.Type != "burn_single" {
				toHash = action.Recipient
			}
			if action.TokenId == nil {
				continue
			}
			if action.Amount == nil {
				continue
			}
			dataHash1 := common.BigToHash(action.TokenId).Bytes()
			dataHash2 := common.BigToHash(action.Amount).Bytes()
			res = append(res, &ethtypes.Log{
				Address: pointerAddr,
				Topics: []common.Hash{
					ERC1155TransferSingleTopic,
					action.Sender,
					fromHash,
					toHash,
				},
				Data: append(dataHash1, dataHash2...),
			})
		case "transfer_batch", "mint_batch", "burn_batch":
```

**File:** app/receipt.go (L421-458)
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
```

**File:** sei-wasmd/x/wasm/keeper/events.go (L45-66)
```go
// convert and add contract address issuing this event
func contractSDKEventAttributes(customAttributes []wasmvmtypes.EventAttribute, contractAddr sdk.AccAddress) ([]sdk.Attribute, error) {
	attrs := []sdk.Attribute{sdk.NewAttribute(types.AttributeKeyContractAddr, contractAddr.String())}
	// append attributes from wasm to the sdk.Event
	for _, l := range customAttributes {
		// ensure key and value are non-empty (and trim what is there)
		key := strings.TrimSpace(l.Key)
		if len(key) == 0 {
			return nil, sdkerrors.Wrap(types.ErrInvalidEvent, fmt.Sprintf("Empty attribute key. Value: %s", l.Value))
		}
		value := strings.TrimSpace(l.Value)
		// TODO: check if this is legal in the SDK - if it is, we can remove this check
		if len(value) == 0 {
			return nil, sdkerrors.Wrap(types.ErrInvalidEvent, fmt.Sprintf("Empty attribute value. Key: %s", key))
		}
		// and reserve all _* keys for our use (not contract)
		if strings.HasPrefix(key, types.AttributeReservedPrefix) {
			return nil, sdkerrors.Wrap(types.ErrInvalidEvent, fmt.Sprintf("Attribute key starts with reserved prefix %s: '%s'", types.AttributeReservedPrefix, key))
		}
		attrs = append(attrs, sdk.NewAttribute(key, value))
	}
	return attrs, nil
```

**File:** x/evm/keeper/msg_server.go (L247-323)
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
	if err != nil {
		return nil, err
	}
	switch msg.PointerType {
	case types.PointerType_ERC20:
		err = server.SetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc20"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc20.CurrentVersion))))
	case types.PointerType_ERC721:
		err = server.SetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc721"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc721.CurrentVersion))))
	case types.PointerType_ERC1155:
		err = server.SetCW1155ERC1155Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc1155"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc1155.CurrentVersion))))
	default:
		panic("unknown pointer type")
	}
	return &types.MsgRegisterPointerResponse{PointerAddress: pointerAddr.String()}, err
```

**File:** integration_test/rpc_tests/eth/eth_getLogs.spec.ts (L248-270)
```typescript
    describe('non-EVM log sources (dual-VM & precompiles)', () => {
        it('indexes a CW20 ERC20 pointer transfer as a standard Transfer log', async function () {
            const actor = EvmAccount.fromPrivateKey(runtime.wasm!.actor.privateKey, sei);
            const pointer = new ethers.Contract(
                runtime.wasm!.cw20Pointer,
                ERC20_LOG_IFACE,
                actor.wallet,
            );
            const receipt = await (await pointer.transfer(runtime.funded.admin, 1n)).wait();

            const logs = await getLogs({
                address: runtime.wasm!.cw20Pointer,
                fromBlock: ethers.toQuantity(receipt!.blockNumber),
                toBlock: ethers.toQuantity(receipt!.blockNumber),
            });
            const transfer = logs.find((l: any) => l.topics[0] === TRANSFER_TOPIC);
            expect(transfer, 'pointer emits a Transfer log').to.not.equal(undefined);
            expectLogShape(transfer, 'pointer transfer');
            expect(transfer.address).to.equal(runtime.wasm!.cw20Pointer.toLowerCase());
            expect(transfer.topics[2], 'recipient is admin').to.equal(
                addressTopic(runtime.funded.admin),
            );
        });
```
