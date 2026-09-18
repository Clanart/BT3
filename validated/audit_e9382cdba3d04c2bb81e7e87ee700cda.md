### Title
Permissionless CW20 pointer registration lets a malicious contract forge synthetic ERC20 `Transfer` events with fabricated amounts - ([File: app/receipt.go])

### Summary
The nkpaymentcap incident was a "fake transfer notification" attack: the DApp trusted an `on_transfer` action without verifying it represented a real, verified value movement, letting the attacker spoof deposit notifications and drain real tokens. Sei-chain has an analogous trust gap in how it synthesizes ERC20 `Transfer` logs for CW20 tokens that have EVM pointers registered.

### Finding Description
Any EVM caller can permissionlessly create a pointer for an arbitrary CosmWasm contract via `addCW20Pointer`, the only precondition being that the target contract answers a `token_info` query, as seen in `precompiles/pointer/legacy/v552/pointer.go` `AddCW20` [1](#0-0) . There is no requirement that the wasm contract's execute handlers enforce real CW20 semantics (i.e. actually debiting/crediting balances) — an attacker can deploy a lightweight wasm contract that merely mimics the CW20 `token_info` query and freely emits arbitrary `wasm` module events.

On the Sei side, `app.AddCosmosEventsToEVMReceiptIfApplicable` looks up any registered CW20→ERC20 pointer for the contract that emitted a `wasm` event and unconditionally converts that event's `action`/`from`/`to`/`amount` attributes into a synthetic ERC20 `Transfer` log via `translateCW20Event`: [2](#0-1) [3](#0-2) .

`translateCW20Event` does not verify the emitted amount against an actual balance delta of the CW20 contract, nor does it re-query the contract's `balance` before/after — it directly trusts whatever `mint`, `burn`, `send`, `transfer`, `transfer_from`, `send_from`, or `burn_from` action attributes the wasm event carries. Because these are just standard `wasm` events (attribute key/value pairs any CosmWasm contract can emit at will inside any `execute` entrypoint), a malicious contract with a registered pointer can emit an event that looks exactly like `action=transfer, from=<victim>, to=<attacker>, amount=<huge>` without any real CW20 balance change occurring in its own contract storage.

The resulting synthetic `Transfer(from, to, amount)` log is then attached to the EVM transaction/shell receipt and becomes visible via `eth_getLogs`/`eth_getTransactionReceipt`, indistinguishable from any other legitimate pointer-emitted transfer as confirmed by tests such as `integration_test/rpc_tests/eth/eth_getLogs.spec.ts` (`'indexes a CW20 ERC20 pointer transfer as a standard Transfer log'`), which shows exactly this synthetic log path is a first-class, RPC-visible feature [4](#0-3) .

### Impact Explanation
Any downstream integrator (bridges, CEXs, DeFi protocols, indexers) that watches `eth_getLogs`/receipts for ERC20 `Transfer` events emitted by a CW20 pointer address to credit deposits or trigger settlement logic can be deceived into believing a token transfer of an arbitrary amount occurred, when no corresponding value movement took place in the underlying CW20 contract's actual balance state. This is the same "fake transfer notification" root cause as the nkpaymentcap incident — a listener trusts a transfer-shaped notification instead of verifying real state change — and can lead to unauthorized crediting of funds/fake deposits against real assets (e.g., a bridge or exchange minting/crediting real value based on the forged Transfer log).

### Likelihood Explanation
The attack requires only: (1) permissionless deployment of a CosmWasm contract that fakes `token_info`, (2) a permissionless `addCW20Pointer` registration transaction, and (3) an `execute` call on the attacker's own contract that emits a `wasm` event with the right attributes. All three steps are reachable by any unprivileged transaction sender with no special permissions, making this readily exploitable by anyone willing to pay gas for a contract deployment and pointer registration.

### Recommendation
`translateCW20Event` (and the analogous CW721/CW1155 translators) should not trust `from`/`to`/`amount` fields taken directly from arbitrary `wasm` event attributes. Instead, synthetic Transfer/Approval logs should be derived from, or cross-checked against, actual state queries (e.g., `balance`/`allowance` before and after execution) similar to how `increase_allowance`/`decrease_allowance` already re-query `allowance` from the contract rather than trusting an emitted delta. Additionally, consider restricting `addCW20Pointer` (and its CW721/CW1155 counterparts) to contracts that can be verified to implement the standard CW20 interface soundly (e.g., via code-hash allowlisting or governance-gated registration for non-standard code), reducing the attack surface for spoofed pointee contracts.

### Proof of Concept
1. Deploy a CosmWasm contract `FakeCW20` whose `token_info` query returns a plausible name/symbol/decimals but whose `execute` handler for any message simply emits: `wasm` event with `action=transfer`, `from=<attacker-controlled seiAddr>`, `to=<attacker-controlled seiAddr2>`, `amount=1000000000000` — with no actual balance bookkeeping.
2. Call `addCW20Pointer(FakeCW20Address)` via the pointer precompile (`precompiles/pointer/pointer.go`) from any EOA; this succeeds because only a `token_info` response is required.
3. Invoke any execute entrypoint on `FakeCW20` that emits the fabricated `transfer` event.
4. Observe via `eth_getLogs`/`eth_getTransactionReceipt` that a synthetic ERC20 `Transfer(from, to, 1000000000000)` log is produced for the pointer address, as validated by the pattern in `app.translateCW20Event` [5](#0-4)  and exercised by the existing test `TestEvmEventsForCw20` in `app/receipt_test.go`, confirming the log is emitted purely from wasm event attributes without any real balance verification [6](#0-5) .

### Citations

**File:** precompiles/pointer/legacy/v552/pointer.go (L196-238)
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
	constructorArguments := []interface{}{
		cwAddr, name, symbol,
	}

	packedArgs, err := cw20.GetParsedABI().Pack("", constructorArguments...)
	if err != nil {
		panic(err)
	}
	bin := append(cw20.GetBin(), packedArgs...)
	if value == nil {
		value = utils.Big0
	}
	ret, contractAddr, remainingGas, err := evm.Create(caller, bin, suppliedGas, uint256.MustFromBig(value))
	if err != nil {
		return
	}
	err = p.evmKeeper.SetERC20CW20Pointer(ctx, cwAddr, contractAddr)
```

**File:** app/receipt.go (L73-94)
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

**File:** app/receipt_test.go (L38-82)
```go
func TestEvmEventsForCw20(t *testing.T) {
	k := testkeeper.EVMTestApp.EvmKeeper
	wasmKeeper := k.WasmKeeper()
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now()).WithChainID("sei-test").WithBlockHeight(1)
	code, err := os.ReadFile("../contracts/wasm/cw20_base.wasm")
	require.Nil(t, err)
	privKey := testkeeper.MockPrivateKey()
	creator, _ := testkeeper.PrivateKeyToAddresses(privKey)
	codeID, err := wasmKeeper.Create(ctx, creator, code, nil)
	require.Nil(t, err)
	contractAddr, _, err := wasmKeeper.Instantiate(ctx, codeID, creator, creator, []byte(fmt.Sprintf("{\"name\":\"test\",\"symbol\":\"test\",\"decimals\":6,\"initial_balances\":[{\"address\":\"%s\",\"amount\":\"1000000000\"}]}", creator.String())), "test", sdk.NewCoins())
	require.Nil(t, err)

	_, mockPointerAddr := testkeeper.MockAddressPair()
	k.SetERC20CW20Pointer(ctx, contractAddr.String(), mockPointerAddr)

	// calling CW contract directly
	amt := sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(1000000000000)))
	k.BankKeeper().MintCoins(ctx, "evm", amt)
	k.BankKeeper().SendCoinsFromModuleToAccount(ctx, "evm", creator, amt)
	recipient, _ := testkeeper.MockAddressPair()
	payload := []byte(fmt.Sprintf("{\"transfer\":{\"recipient\":\"%s\",\"amount\":\"100\"}}", recipient.String()))
	msg := &wasmtypes.MsgExecuteContract{
		Sender:   creator.String(),
		Contract: contractAddr.String(),
		Msg:      payload,
	}
	txBuilder := testkeeper.EVMTestApp.GetTxConfig().NewTxBuilder()
	txBuilder.SetMsgs(msg)
	txBuilder.SetFeeAmount(sdk.NewCoins(sdk.NewCoin("usei", sdk.NewInt(1000000))))
	txBuilder.SetGasLimit(300000)
	tx := signTx(txBuilder, privKey, k.AccountKeeper().GetAccount(ctx, creator))
	txbz, err := testkeeper.EVMTestApp.GetTxConfig().TxEncoder()(tx)
	require.Nil(t, err)
	sum := sha256.Sum256(txbz)
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
