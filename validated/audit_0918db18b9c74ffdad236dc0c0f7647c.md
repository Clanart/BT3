Confirmed: `CallEVM` in `x/evm/keeper/evm.go` only checks `res.Err` (EVM-level revert), and returns `res.ReturnData` without any ABI decoding. Neither `HandleInternalEVMCall`/`HandleInternalEVMDelegateCall` nor the CW20/CW721/CW1155 wrapper contracts that construct `EvmMsg::DelegateCallEvm` (`example/cosmwasm/cw20/src/contract.rs` `transfer`/`transfer_from`) inspect the ABI-encoded `bool` returned by the target ERC20's `transfer`/`transferFrom`. This matches the reported bug class (unchecked ERC20 return value) and is reachable by any CosmWasm contract/user invoking the CW20-pointer's transfer messages.

### Title
Unchecked ERC20 `transfer`/`transferFrom` boolean return value in CW↔EVM pointer bridge - (File: example/cosmwasm/cw20/src/contract.rs)

### Summary
The CW20 pointer contract for ERC20 tokens builds a `transfer`/`transferFrom` ABI payload and sends it to the EVM via `EvmMsg::DelegateCallEvm`, which is executed by `Keeper.CallEVM` in `x/evm/keeper/evm.go`. Neither the wasm contract nor the Go handler decodes/validates the ERC20 `bool` return value; only a low-level EVM revert (`res.Err`) is treated as failure.

### Finding Description
`transfer` and `transfer_from` in `example/cosmwasm/cw20/src/contract.rs` build the ERC20 call payload via `EvmQuerier::erc20_transfer_payload`/`erc20_transfer_from_payload` and wrap it in `EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload }` [1](#0-0) , [2](#0-1) . This message is routed through `EncodeDelegateCallEVM` into a `MsgInternalEVMDelegateCall` [3](#0-2) , handled by `HandleInternalEVMDelegateCall`, which ultimately calls `k.CallEVM` [4](#0-3) . `CallEVM` treats the call as successful as long as `res.Err == nil` and returns the raw `res.ReturnData` unexamined [5](#0-4) . Neither this Go path nor the Rust contract decodes the ABI-encoded `bool` that a standards-compliant ERC20 `transfer`/`transferFrom` returns. Any ERC20 token that returns `false` on failure instead of reverting (a well-known non-standard-but-common ERC20 pattern, e.g. old USDT-style tokens or intentionally malicious tokens) will cause the wasm contract to emit a successful `Response` with `"action": "transfer"`/`"transfer_from"` attributes even though no tokens moved.

### Impact Explanation
Any CosmWasm contract or composed protocol relying on the CW20 pointer's `Transfer`/`TransferFrom` response (attributes/events) as proof-of-payment will treat a failed transfer as successful, since the response is generated unconditionally once the delegatecall doesn't revert. This can lead to accounting desynchronization between the CW20 pointer's reported balances/events and the true ERC20 balances, and downstream contracts (e.g. escrow, vesting, payment-gated logic built atop the pointer) can release funds or mark obligations paid without an actual transfer occurring — a direct fund-loss vector when interacting with non-standard or malicious ERC20 tokens through the pointer.

### Likelihood Explanation
Reachable by any CosmWasm user who can execute `Transfer`/`TransferFrom`/`Send`/`SendFrom` on a CW20↔ERC20 pointer contract, requiring only a targeted ERC20 token that returns `false` rather than reverting on failure — no special privileges needed. The condition depends on the specific target ERC20's implementation, which is somewhat token-dependent but is a well-documented and common class of tokens.

### Recommendation
In `example/cosmwasm/cw20/src/contract.rs` (and the analogous CW721/CW1155 wrappers), decode the return data of the delegatecall (or have `CallEVM`/`HandleInternalEVMDelegateCall` in `x/evm/keeper/evm.go` propagate and check the ABI-decoded boolean for `transfer`/`transferFrom` calls) and return an error/abort the CosmWasm execution if the return value is `false`, mirroring the recommended SafeERC20-style checked-return pattern from the original report.

### Proof of Concept
1. Deploy or point a CW20 wrapper (`example/cosmwasm/cw20`) at a non-standard ERC20 contract whose `transfer`/`transferFrom` returns `false` on failure conditions (e.g., paused, blacklisted, insufficient allowance edge cases) instead of reverting.
2. Call `ExecuteMsg::Transfer` on the CW20 pointer under a failure condition of the underlying token.
3. Observe: `execute_transfer` -> `transfer` in `contract.rs` unconditionally builds and returns a successful `Response` with `add_attribute("action", "transfer")` [6](#0-5) ; the delegatecall inside `CallEVM` succeeds at the EVM-execution level (`res.Err == nil`) since the token merely returned `false` rather than reverting [7](#0-6) .
4. A downstream contract or off-chain observer trusting this success response would incorrectly assume the transfer occurred.

### Citations

**File:** example/cosmwasm/cw20/src/contract.rs (L83-93)
```rust
pub fn execute_transfer(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer(deps, _env, info, recipient, amount)?;
    res = res.add_attribute("action", "transfer");
    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L227-248)
```rust
fn transfer(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_payload(recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", info.sender)
        .add_attribute("to", recipient)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L250-274)
```rust
pub fn transfer_from(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    owner: String,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&owner)?;
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_from_payload(owner.clone(), recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", owner)
        .add_attribute("to", recipient)
        .add_attribute("by", info.sender)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```

**File:** x/evm/client/wasm/encoder.go (L32-53)
```go
func EncodeDelegateCallEVM(rawMsg json.RawMessage, sender sdk.AccAddress, info wasmvmtypes.MessageInfo, codeInfo wasmtypes.CodeInfo) ([]sdk.Msg, error) {
	encodedCallEVM := bindings.DelegateCallEVM{}
	if err := json.Unmarshal(rawMsg, &encodedCallEVM); err != nil {
		return []sdk.Msg{}, err
	}
	decodedData, err := base64.StdEncoding.DecodeString(encodedCallEVM.Data)
	if err != nil {
		return []sdk.Msg{}, err
	}
	s := sender
	if origSender, err := sdk.AccAddressFromBech32(info.Sender); err == nil {
		s = origSender
	}
	internalCallEVMMsg := types.MsgInternalEVMDelegateCall{
		Sender:       s.String(),
		To:           encodedCallEVM.To,
		CodeHash:     codeInfo.CodeHash,
		Data:         decodedData,
		FromContract: sender.String(),
	}
	return []sdk.Msg{&internalCallEVMMsg}, nil
}
```

**File:** x/evm/keeper/evm.go (L47-77)
```go
func (k *Keeper) HandleInternalEVMDelegateCall(ctx sdk.Context, req *types.MsgInternalEVMDelegateCall) (*sdk.Result, error) {
	var to *common.Address
	if req.To != "" {
		addr := common.HexToAddress(req.To)
		to = &addr
	} else {
		return nil, errors.New("cannot use a CosmWasm contract to delegate-create an EVM contract")
	}
	addr, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(req.FromContract))))
	if !exists || common.BytesToAddress(addr).Cmp(*to) != 0 {
		return nil, errors.New("only pointer contract can make delegatecalls")
	}
	zeroInt := sdk.ZeroInt()
	senderAddr, err := sdk.AccAddressFromBech32(req.Sender)
	if err != nil {
		return nil, err
	}
	// delegatecall caller must be associated; otherwise any state change on EVM contract will be lost
	// after they asssociate.
	senderEvmAddr, found := k.GetEVMAddress(ctx, senderAddr)
	if !found {
		err := types.NewAssociationMissingErr(req.Sender)
		evmKeeperMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "evm_handle_internal_evm_delegate_call"), attribute.String("type", err.AddressType())))
		return nil, err
	}
	ret, err := k.CallEVM(ctx, senderEvmAddr, to, &zeroInt, req.Data)
	if err != nil {
		return nil, err
	}
	return &sdk.Result{Data: ret}, nil
}
```

**File:** x/evm/keeper/evm.go (L112-152)
```go
	res, err := k.applyEVMMessage(ctx, evmMsg, stateDB, gp, false)
	if err != nil {
		return nil, err
	}
	k.consumeEvmGas(ctx, res.UsedGas)
	if res.Err != nil {
		return nil, res.Err
	}
	surplus, err := stateDB.Finalize()
	if err != nil {
		return nil, err
	}
	vmErr := ""
	if res.Err != nil {
		vmErr = res.Err.Error()
	}
	existingReceipt, err := k.GetTransientReceipt(ctx, ctx.TxSum(), uint64(ctx.TxIndex())) //nolint:gosec
	if err == nil {
		for _, l := range existingReceipt.Logs {
			stateDB.AddLog(&ethtypes.Log{
				Address: common.HexToAddress(l.Address),
				Topics:  utils.Map(l.Topics, common.HexToHash),
				Data:    l.Data,
			})
		}
		if existingReceipt.VmError != "" {
			vmErr = fmt.Sprintf("%s\n%s\n", existingReceipt.VmError, vmErr)
		}
	}
	existingDeferredInfo, found := k.GetEVMTxDeferredInfo(ctx)
	if found {
		surplus = surplus.Add(existingDeferredInfo.Surplus)
	}
	receipt, err := k.WriteReceipt(ctx, stateDB, evmMsg, ethtypes.LegacyTxType, ctx.TxSum(), res.UsedGas, vmErr, false)
	if err != nil {
		return nil, err
	}
	bloom := ethtypes.Bloom{}
	bloom.SetBytes(receipt.LogsBloom)
	k.AppendToEvmTxDeferredInfo(ctx, bloom, ctx.TxSum(), surplus)
	return res.ReturnData, nil
```
