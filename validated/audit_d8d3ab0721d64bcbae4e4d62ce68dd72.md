### Title
Permissionless CW pointer registration lets any wasm contract author emit fabricated ERC20/ERC721/ERC1155 events in EVM receipts - (File: `app/receipt.go`)

### Summary
The `Emitter` bug class described in the report is "a privileged event-emission role can be reached through an unrelated generic-call surface, letting an attacker emit deceptive events that off-chain logic trusts." Sei-chain has a directly analogous, and actually more broadly reachable, primitive: any account can permissionlessly register a CosmWasm contract as an ERC20/ERC721/ERC1155 "pointer" via the `pointer` precompile, and the chain then mechanically translates *whatever wasm module events that CW contract chooses to emit* into synthetic Ethereum logs attached to the pointer's EVM address, exposed through `eth_getLogs`/transaction receipts with no verification that the emitted attributes correspond to any real balance movement.

### Finding Description
The `pointer` precompile exposes `addCW20Pointer` / `addCW721Pointer` / `addCW1155Pointer`, which register an arbitrary Sei bech32 contract address as the CW-backed counterpart of a freshly deployed ERC pointer contract. The registration only requires the target contract to answer a `token_info`/similar query — it performs no validation of the contract's actual execute-message semantics or that its wasm events genuinely reflect balance changes: [1](#0-0) 

Anyone can therefore deploy a trivial CosmWasm contract that: (a) satisfies the `token_info` query needed for pointer creation, and (b) has an arbitrary `execute` entrypoint that simply emits a `wasm` module event with attributes shaped like `action=transfer/mint/burn`, `from`, `to`, `amount` — regardless of whether any coins or CW20 balances actually moved.

After any transaction that produces `wasm` module events, `app.AddCosmosEventsToEVMReceiptIfApplicable` scans those events, looks up whether the emitting contract address has a registered ERC20/721/1155 pointer, and if so mechanically synthesizes Ethereum logs (`Transfer`, etc.) at the pointer's EVM address purely from the event's attributes: [2](#0-1) 

The `translateCW20Event` function directly converts `mint/burn/send/transfer/transfer_from/send_from/burn_from` wasm-event actions into `ERC20TransferTopic` logs using the `From`/`To`/`Amount` values taken straight from the contract-controlled event attributes, with no cross-check against the CW20 contract's actual balance state: [3](#0-2) 

Similar unchecked translation exists for CW721 (`transfer_nft`, `send_nft`, `burn`, `mint`) and CW1155 events, all keyed only off the contract-authored event attributes.

This mirrors the reported bug precisely: an unprivileged actor (here, any wasm contract deployer/executor — no `EMITTER`-equivalent role is even required) can trigger "genuine-looking" `Transfer`/mint/burn events on an EVM address that off-chain infrastructure (indexers, bridges, exchange balance trackers, wallets calling `eth_getLogs`) will treat as authoritative ERC20/721 activity, without any real token movement having occurred.

### Impact Explanation
Off-chain systems (CEXes, bridges, portfolio trackers, automated market makers) that rely on `eth_getLogs`/receipt `Transfer` events to credit deposits or update balances can be deceived into believing large synthetic mint/transfer events occurred on a pointer contract that has no relationship to real backing value, since the attacker fully controls the wasm contract's emitted attributes (from/to/amount) independent of actual CW20/CW721/CW1155 state. This is the same "deceive off-chain logic via fabricated events" impact class as the referenced Emitter finding, but on Sei it is reachable via a fully permissionless, non-privileged action (deploy a CW contract + call `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` + execute the contract), rather than requiring a compromised or malicious DAO proxy.

### Likelihood Explanation
High. Registering a pointer for an arbitrary wasm contract is unauthenticated and requires no special role — any address can call the `pointer` precompile's `addCW20Pointer`/`addCW721Pointer`/`addCW1155Pointer` methods after deploying a minimal CW20/721/1155-lookalike contract. Crafting a contract that emits fabricated `mint`/`transfer`/`burn` wasm attributes without moving real balances is a straightforward CosmWasm exercise.

### Recommendation
Do not translate wasm module events into synthetic EVM logs purely from event attributes emitted by the contract itself; instead verify the translated action against the CW keeper's authoritative state change (e.g., re-query balances or restrict translation to internally-generated, keeper-emitted events rather than contract-controlled `wasm` events), or restrict pointer registration/pointer event translation to whitelisted/gov-approved contracts similar to the native-denom pointer path, which requires bank module metadata rather than an arbitrary contract-controlled response.

### Proof of Concept
1. Deploy a minimal CosmWasm contract `Fake20` whose `token_info` query returns valid CW20 metadata, and whose `execute` handler for any message simply emits a `wasm` event with `action=transfer`, `from=<attacker>`, `to=<victim/exchange-deposit-address>`, `amount=<huge number>` (mirroring the standard cw20-base event schema) without touching any real balance.
2. Call the `pointer` precompile's `addCW20Pointer(Fake20Address)` — permissionless, no role required — to obtain `pointerAddr`, an ERC20 contract shadow of `Fake20`, per `precompiles/pointer/pointer.go`.
3. Execute the fake `transfer` message on `Fake20` via the `wasmd` precompile or a native `MsgExecuteContract`.
4. `app.AddCosmosEventsToEVMReceiptIfApplicable` (`app/receipt.go`) detects the `wasm` event, resolves the CW20 pointer, and appends a synthetic `Transfer(from, to, amount)` log at `pointerAddr` to the transaction's EVM receipt/`eth_getLogs` output, even though `Fake20`'s true balances never changed.
5. An off-chain consumer polling `eth_getLogs` for `Transfer` events at `pointerAddr` (treating it as a legitimate ERC20 token) observes the deceptive transfer and can be tricked into crediting the victim account.

### Citations

**File:** precompiles/pointer/pointer.go (L99-120)
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
```

**File:** app/receipt.go (L73-104)
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
	}
```

**File:** app/receipt.go (L147-169)
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
```
