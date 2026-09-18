### Title
CW20-wrapper for arbitrary ERC20 pointers treats non-reverting "weird" ERC20 transfer failures as success - ([File: example/cosmwasm/cw20/src/contract.rs])

### Summary
The CW20 wrapper contract that lets CosmWasm callers move an underlying arbitrary EVM ERC20 token (the `x/evm/artifacts/erc20` "ERC20 tokens get a CW20 wrapper" pointer, per `x/evm/AGENTS.md`) only checks whether the low-level `DelegateCallEvm` reverted, never whether the wrapped ERC20's `transfer`/`transferFrom` actually returned `true`. This is the same bug class as the external report: checking call-level success but not the ERC20 boolean return value, which fails silently for "weird" ERC20 tokens (`MissingReturns`, `ReturnsFalse`).

### Finding Description
`transfer` and `transfer_from` in the CW20 wrapper build an ABI-encoded `transfer`/`transferFrom` payload via `EvmQuerier::erc20_transfer_payload` / `erc20_transfer_from_payload`, and simply queue an `EvmMsg::DelegateCallEvm{to: erc_addr, data: payload.encoded_payload}` message without ever inspecting the return data: [1](#0-0) [2](#0-1) 

The `DelegateCallEvm` message is routed through `x/evm/client/wasm/encoder.go`'s `EncodeDelegateCallEVM` into `MsgInternalEVMDelegateCall`, which is handled by `Keeper.HandleInternalEVMDelegateCall` → `Keeper.CallEVM`: [3](#0-2) [4](#0-3) 

`CallEVM` only propagates an error when `res.Err != nil` (an actual EVM revert/exception) and otherwise returns `res.ReturnData` as-is: [5](#0-4) 

Nowhere in this path — not in the Rust wrapper contract, not in `HandleInternalEVMDelegateCall`, not in `CallEVM` — is the ABI-decoded boolean return value of `transfer`/`transferFrom` checked, nor is the "no return data at all" case handled. If the target ERC20 token is a "weird" implementation that either (a) returns `false` without reverting, or (b) has no return value at all (both explicitly called out in the linked external report as `ReturnsFalse`/`MissingReturns`), `res.Err` remains `nil`, `CallEVM` succeeds, and the wrapper contract's `Response` is built and committed with `add_attribute("amount", amount)` implying success — even though the underlying token balance was never moved.

### Impact Explanation
Any CosmWasm caller (a wasm message sender) using the CW20 wrapper to transfer such a non-conforming ERC20 pointer token would have on-chain wrapper-side attributes/events indicating a successful transfer while the real ERC20 balance never moved. This can be leveraged for fund-loss/accounting-desync exploits: e.g., a deposit flow that treats the wrapper's successful `transfer_from` completion as proof of collateral movement could be fooled into crediting a CW-side balance/vault entry without ever receiving the underlying ERC20 tokens, or a user could be told a withdrawal succeeded while their tokens remain unmoved (permanent loss/freezing of expected funds from the counterparty's perspective).

### Likelihood Explanation
Reachable purely by an unprivileged CosmWasm/wasm message sender: any address that can register an ERC20→CW20 pointer for an arbitrary EVM contract and then interacts with it via the standard wrapper's `transfer`/`transfer_from` execute messages can trigger this path. No special privileges, validator collusion, or network conditions are required — only deploying (or picking an existing) "weird ERC20" contract as the pointer target, which is explicitly permitted since pointers can be created for arbitrary EVM ERC20 contracts.

### Recommendation
In the CW20 wrapper's `transfer`/`transfer_from` (and any other ERC20 pointer operations that rely on `DelegateCallEvm` returning a boolean), decode the ABI return data and require it to be non-empty and `true`, matching the standard `SafeERC20`-style pattern: treat empty return data as success only if the call didn't revert AND is documented as optional-return, but explicitly reject `false`. Concretely, this requires the EVM-side query handler (`x/evm/client/wasm/query.go` `HandleERC20TransferPayload`/`HandleERC20TransferFromPayload`) or the wrapper contract to also expose/consume the decoded boolean from `CallEVM`'s `res.ReturnData`, and fail the CW message (return `ContractError`) if the decoded value is `false` or if return data is non-empty but not decodable as `true`.

### Proof of Concept
1. Deploy a minimal ERC20-like EVM contract whose `transfer`/`transferFrom` always returns `false` (or returns no data) without reverting — mirroring `ReturnsFalse.sol`/`MissingReturns.sol` from the referenced weird-erc20 repo.
2. Register a CW20-wrapper pointer for that EVM contract (standard "ERC20 tokens get a CW20 wrapper" flow).
3. Have an attacker/victim account call the wrapper's `transfer` (or `transfer_from`) execute message moving tokens to another CW/EVM account.
4. Observe: `CallEVM` returns with `res.Err == nil` (no revert) and the CW wrapper's `Response` succeeds with `from`/`to`/`amount` attributes and (if applicable) a `Transfer` event, while the underlying ERC20 contract's internal balances are unchanged (since the transfer logic returned `false`/no-op). Any downstream contract or off-chain consumer trusting the wrapper's execute success is misled about the actual token movement.

### Citations

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

**File:** x/evm/keeper/evm.go (L79-153)
```go
func (k *Keeper) CallEVM(ctx sdk.Context, from common.Address, to *common.Address, val *sdk.Int, data []byte) (retdata []byte, reterr error) {
	if ctx.IsEVM() && !ctx.EVMEntryViaWasmdPrecompile() {
		return nil, errors.New("sei does not support EVM->CW->EVM call pattern")
	}
	if to == nil && len(data) > params.MaxInitCodeSize {
		return nil, fmt.Errorf("%w: code size %v, limit %v", core.ErrMaxInitCodeSizeExceeded, len(data), params.MaxInitCodeSize)
	}
	if to != nil && to.Cmp(common.HexToAddress(solo.SoloAddress)) == 0 {
		return nil, errors.New("cannot call Solo precompile via CosmWasm")
	}
	value := utils.Big0
	if val != nil {
		if val.IsNegative() {
			return nil, sdkerrors.ErrInvalidCoins
		}
		value = val.BigInt()
	}
	// This call was not part of an existing StateTransition, so it should trigger one
	executionCtx := ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx)).WithEVMEntryViaWasmdPrecompile(false)
	stateDB := state.NewDBImpl(executionCtx, k, false)
	gp := k.GetGasPool()
	evmMsg := &core.Message{
		Nonce:     stateDB.GetNonce(from), // replay attack is prevented by the AccountSequence number set on the CW transaction that triggered this call
		GasLimit:  k.getEvmGasLimitFromCtx(ctx),
		GasPrice:  utils.Big0, // fees are already paid on the CW transaction
		GasFeeCap: utils.Big0,
		GasTipCap: utils.Big0,
		To:        to,
		Value:     value,
		Data:      data,
		From:      from,
	}
	// should not increment nonce since this isn't a transaction
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
}
```
