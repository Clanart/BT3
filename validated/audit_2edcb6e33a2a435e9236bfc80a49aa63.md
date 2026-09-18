## Title
CW20 wasm events with attacker-controlled `amount` attribute are trusted to synthesize ERC20 `Transfer` logs, deceiving `eth_getLogs`/receipt consumers into believing a deposit occurred - (File: `app/receipt.go`)

### Summary
`app.AddCosmosEventsToEVMReceiptIfApplicable` scans every Cosmos wasm event in a transaction and, for any contract address that has a registered CW20→ERC20 pointer, calls `translateCW20Event` to synthesize an EVM `Transfer(address,address,uint256)` log for the pointer address. The synthesized log's `amount` (and `from`/`to`) are taken verbatim from the wasm contract's own emitted event attributes (`action`, `amount`, `from`, `to`) rather than being derived from, or cross-checked against, an actual verified balance change in the bank/wasm state. This is structurally the same bug class as the Monero report: a state-changing amount that downstream consumers (there, a wallet; here, `eth_getLogs`/tx receipt consumers such as an exchange indexer) implicitly trust as "the amount that moved" is instead attacker-supplied data with no consensus-level tie to real value transfer.

### Finding Description
The flow is:
1. `AddCosmosEventsToEVMReceiptIfApplicable` collects all wasm events of type `wasm` from the tx and, for each, looks up whether the event's `contractAddr` has a registered CW20→ERC20 pointer via `GetERC20CW20Pointer`. [1](#0-0) 
2. For matching contracts, `translateCW20Event` iterates the "actions" parsed out of the raw wasm event attributes (`action`, `amount`, `from`, `to`, etc., extracted purely from attribute key/value strings by `GetActionsFromWasmEvent`) and, for any action type in `{"mint","burn","send","transfer","transfer_from","send_from","burn_from"}`, directly emits a synthetic `Transfer` log whose `Data` is `action.Amount` and whose indexed `from`/`to` are `action.From`/`action.To` — all sourced from the event attributes themselves. [2](#0-1) [3](#0-2) 
3. Nowhere in this path is the emitted `amount`/`from`/`to` reconciled against an actual balance query (e.g. `QuerySmart` for `balance`) or against the underlying bank/wasm state delta. Contrast this with the `increase_allowance`/`decrease_allowance` branch in the same function, which *does* re-query the contract for the real allowance value before emitting a log — showing the codebase is aware that emitted event fields can't always be trusted, yet the transfer/mint/burn amount path skips this check entirely. [4](#0-3) 

Any CosmWasm contract can be pointed to by an ERC20 pointer (`AddCW20Pointer`/`AddCW20`), since the precompile only requires the contract to answer a `token_info` query; it does not restrict pointee contracts to a verified, non-malicious CW20 implementation. [5](#0-4) 

An attacker who deploys their own CosmWasm contract, gets an ERC20 pointer created for it, and then executes a message on that contract that simply emits a `wasm` event with `action=transfer`, `amount=<huge value>`, `from=<attacker>`, `to=<exchange-controlled address>` — without moving any real coins/CW20 balance — will cause the node to synthesize a legitimate-looking `Transfer` log at the pointer's ERC20 address for that (fabricated) amount. This log is returned via standard `eth_getLogs`/transaction receipts, exactly the channel most exchanges/indexers use to detect ERC20 deposits.

### Impact Explanation
This is directly analogous to the Monero report's core mechanism: a value shown to an external, trust-relying observer (there: wallet-decoded RingCT amount for a zero-verified-amount TX; here: a synthetic `Transfer` log amount for an event that didn't correspond to any real balance movement) is fully attacker-controlled and disconnected from actual verified fund movement. If an exchange or custodial service credits deposits based on watching `Transfer` events / `eth_getLogs` on ERC20 pointer contracts (a normal integration pattern, and one this codebase explicitly supports via `translateCW20Event`), an attacker can fabricate an arbitrary deposit amount and have it credited, then withdraw real value elsewhere — a direct unauthorized-transfer/fund-loss primitive via a CW<->EVM pointer bridge, which is explicitly in scope.

### Likelihood Explanation
Reachable by any unprivileged user: deploying a CosmWasm contract and registering it as a CW20 pointer are both permissionless actions (`AddCW20Pointer` requires only a `token_info` query success), and emitting a crafted `wasm` event with `action`/`amount`/`from`/`to` attributes requires no special contract logic beyond `deps.api.debug`/event attribute construction in a `execute` handler — well within reach of a single crafted transaction.

### Recommendation
In `translateCW20Event`, do not trust the `amount`/`from`/`to` fields taken directly from wasm event attributes for balance-affecting actions (`mint`, `burn`, `send`, `transfer`, `transfer_from`, `send_from`, `burn_from`). Instead, either (a) re-query the CW20 contract's authoritative `balance` (as is already done for allowances) before and after to compute the true delta, or (b) require these synthetic logs be derived only from bank/wasm-module-verified state transitions (e.g., only for pointer contracts backed by module-controlled bookkeeping), not from arbitrary contract-emitted attribute strings.

### Proof of Concept
1. Deploy a minimal CosmWasm contract with an `execute` entrypoint `fake_transfer` that responds with `Response::new().add_attribute("action","transfer").add_attribute("from", attacker).add_attribute("to", victim_exchange_addr).add_attribute("amount","1000000000000")`, without touching any CW20 balances.
2. Call the `pointer` precompile's `AddCW20Pointer` for this contract's Sei address (only requires it to answer `token_info`), obtaining a pointer EVM address.
3. Execute `fake_transfer` via a normal tx.
4. Query `eth_getLogs`/`eth_getTransactionReceipt` for the tx: observe a well-formed `Transfer(from=attacker, to=victim, amount=1000000000000)` log at the pointer address, with no corresponding real CW20 balance/state change, matching `app/receipt.go`'s `translateCW20Event` logic at [2](#0-1) .

### Citations

**File:** app/receipt.go (L73-85)
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

**File:** app/receipt.go (L169-194)
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

**File:** precompiles/pointer/legacy/v552/pointer.go (L196-221)
```go
func (p Precompile) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)
	if exists {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw20.CurrentVersion(ctx))
	}
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
```
