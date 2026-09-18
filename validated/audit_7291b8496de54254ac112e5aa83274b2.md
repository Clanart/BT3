### Title
Malicious CosmWasm contracts can forge synthetic ERC20/721/1155 `Transfer` logs on their own EVM pointer, deceiving off-chain consumers of pointer events - (File: app/receipt.go)

### Summary
`app.AddCosmosEventsToEVMReceiptIfApplicable` synthesizes EVM `Transfer`/`Approval` logs for CW20/CW721/CW1155 contracts that have a registered EVM pointer, by directly trusting arbitrary attribute values (`action`, `from`, `to`, `amount`, `sender`, `recipient`, etc.) taken from the CosmWasm-emitted `wasm`-type event, with **no verification against actual contract balance/state changes**.

### Finding Description
For every `wasm`-type event in a delivered Cosmos tx, `AddCosmosEventsToEVMReceiptIfApplicable` reads `_contract_address` and, if that contract has a registered ERC20/ERC721/ERC1155 pointer (`GetERC20CW20Pointer`/`GetERC721CW721Pointer`/`GetERC1155CW1155Pointer`), calls `translateCW20Event`/`translateCW721Event`/`translateCW1155Event`, which in turn call `GetActionsFromWasmEvent`: [1](#0-0) [2](#0-1) 

`GetActionsFromWasmEvent` blindly scans the attributes of the wasm event for well-known key names (`action`, `from`, `to`, `amount`, `sender`, `recipient`, `owner`, `spender`, `operator`, `token_id`, ...) and builds an `Action` purely from those strings, with no query against the contract's real state: [3](#0-2) 

Unlike the `increase_allowance`/`decrease_allowance` branch, which double-checks the resulting allowance via `QuerySmart` before emitting a log, the `mint`/`burn`/`transfer`/`transfer_from`/`send`/`send_from`/`burn_from` branch emits the synthetic `Transfer` log directly from the attacker-supplied attribute values with zero state verification: [4](#0-3) 

Pointers can be created permissionlessly for any CosmWasm contract that exposes CW20/CW721/CW1155-like query support (as shown by pointer deployment/registration tests), and CosmWasm's `Response.add_attribute` lets a contract emit **arbitrary** key/value pairs in a `wasm` event from any execute entrypoint — the wasm keeper only reserves the leading-underscore `_contract_address` key, not `action`, `from`, `to`, `amount`, etc. Consequently, a contract author can:
1. Deploy a CW20-like contract with metadata mimicking a legitimate/valuable token, and permissionlessly register an ERC20 pointer for it.
2. From any (even otherwise no-op) execute call, emit a custom `wasm` event with attributes `action=transfer`, `from=<victim/self>`, `to=<attacker/exchange-controlled address>`, `amount=<large>`.
3. The chain synthesizes a canonical `Transfer(from, to, amount)` EVM log at the pointer's ERC20 address, indistinguishable from a real transfer via `eth_getLogs`/`eth_subscribe`, even though no CW20 balance actually moved.

This mirrors the CVE's bug class: a client (here, any EVM log consumer — indexers, bridges, custodians, wallets) trusts logs "signed"/attributed by a system-recognized address (the pointer contract, which is system-registered analogous to a "trusted service"), but an unprivileged party (any contract owner) can inject spoofed signals (fabricated attribute values) that the trusting client cannot distinguish from genuine ones.

### Impact Explanation
Any off-chain system that watches `Transfer` events on CW-EVM pointer contracts to credit deposits, update balances, or trigger downstream actions (bridges, custodial wallets, exchanges, portfolio trackers, other DApps) can be deceived into believing a transfer of arbitrary size occurred when it did not. This is a concrete path to unauthorized/spoofed transfer semantics via a pointer contract, satisfying the "unauthorized transfer via precompile or pointer" impact criterion. It does not directly move on-chain funds within the Sei state machine (the CW20 contract's actual balances are untouched), but it forges the canonical EVM event log record that many external systems treat as authoritative proof of an on-chain transfer.

### Likelihood Explanation
Likelihood is high given reachability: deploying a CW20/CW721/CW1155-like contract, registering a pointer, and calling any custom execute message with attacker-chosen attributes is fully permissionless and requires no privileged access — only gas fees. No special validator or governance action is needed.

### Recommendation
- Validate synthesized `Transfer`/`Approval`/`TransferSingle`/`TransferBatch` amounts and addresses against actual contract state before emitting synthetic logs (as is already done for `increase_allowance`/`decrease_allowance` via `QuerySmart`), e.g., diff `balance`/`token_info` query results before/after execution.
- Alternatively, restrict `GetActionsFromWasmEvent` parsing to only trust attributes emitted from the specific sub-message/entrypoint (`execute` message method name) that is expected to correspond to a `transfer`/`mint`/`burn`, rather than any arbitrary event the contract chooses to emit with matching key names.
- Consider cryptographically or structurally tying synthetic log generation to the standard CW20/721/1155 spec's guaranteed event schema rather than free-form attribute matching.

### Proof of Concept
1. Deploy a minimal CosmWasm contract exposing the CW20 query interface expected by the pointer registration flow (`token_info`, `balance`, etc.) with attractive metadata (name/symbol matching a real asset).
2. Register an ERC20 pointer for it via the standard permissionless CW20→ERC20 pointer deployment flow (see `contracts/test/CW20toERC20PointerTest.js`). [5](#0-4) 
3. Implement any execute handler in the contract that calls `Response::new().add_attribute("action","transfer").add_attribute("from", <victim_bech32>).add_attribute("to", <attacker_bech32>).add_attribute("amount", "1000000000000")` without touching any real token balance.
4. Invoke that execute message through a normal `MsgExecuteContract` transaction.
5. Query `eth_getLogs`/subscribe to `logs` on the pointer's ERC20 address — observe a fully-formed `Transfer(victim, attacker, 1e12)` log identical in shape to genuine pointer transfers (as validated by tests such as `app/receipt_test.go`'s `TestEvmEventsForCw20` and `integration_test/rpc_tests/eth/eth_getLogs.spec.ts`'s CW20 pointer log-shape assertions), while `pointer.balanceOf(victim)`/`balanceOf(attacker)` remain unchanged. [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/test/CW20toERC20PointerTest.js (L57-65)
```javascript
            describe("validation", function(){
                it("should not allow a pointer to the pointer", async function(){
                    try {
                        await deployErc20PointerForCw20(hre.ethers.provider, pointer, 5);
                        expect.fail(`Expected to be prevented from creating a pointer`);
                    } catch(e){
                        expect(e.message).to.include("contract deployment failed");
                    }
                });
```

**File:** app/receipt_test.go (L73-82)
```go
	res := testkeeper.EVMTestApp.DeliverTx(ctx.WithEventManager(sdk.NewEventManager()), abci.RequestDeliverTxV2{Tx: txbz}, tx, sum)
	require.Equal(t, uint32(0), res.Code)
	receipt, err := testkeeper.EVMTestApp.EvmKeeper.GetTransientReceipt(ctx, common.BytesToHash(sum[:]), 0)
	require.Nil(t, err)
	require.Equal(t, 1, len(receipt.Logs))
	require.NotEmpty(t, receipt.LogsBloom)
	require.Equal(t, mockPointerAddr.Hex(), receipt.Logs[0].Address)
	_, found := testkeeper.EVMTestApp.EvmKeeper.GetEVMTxDeferredInfo(ctx)
	require.True(t, found)

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
