## Finding: Non-reverting ERC20 tokens can be used to fake successful transfers through the CW20↔ERC20 wrapper

### Title
Unchecked boolean return value from wrapped ERC20 `transfer`/`transferFrom` in the CW20 wrapper contract allows silent-failure tokens to fake successful payments - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The CW20 wrapper for arbitrary ERC20 tokens builds `EvmMsg::DelegateCallEvm` messages to invoke the wrapped ERC20's `transfer`/`transferFrom`, but never inspects the boolean return value of that call. Both the CosmWasm contract and the underlying EVM message-handling path (`HandleInternalEVMDelegateCall` → `CallEVM`) only treat the call as failed if the EVM execution reverts; a token that follows the legacy pattern of returning `false` instead of reverting on failure will make the wrapper report a fully successful transfer even though no tokens moved.

### Finding Description
The `transfer` and `transfer_from` handlers in the CW20-wrapping contract construct the ERC20 call payload and dispatch it as a plain `add_message`, without checking any resulting boolean: [1](#0-0) [2](#0-1) 

This message is routed through the Sei wasm-EVM bridge as `MsgInternalEVMDelegateCall`, handled by `HandleInternalEVMDelegateCall`, which calls `CallEVM` and only surfaces an error when `res.Err != nil` — i.e., only on an actual EVM revert: [3](#0-2) [4](#0-3) 

Notice that `CallEVM` returns `res.ReturnData` directly to the caller without any decoding/verification of the ABI-encoded `bool` that `transfer`/`transferFrom` return: [5](#0-4) 

Because CosmWasm's `add_message` (not a `SubMsg` with `Reply`) is used, the wrapper contract has no visibility into the ERC20 call's return data at all — the CosmWasm transaction as a whole only fails if the delegatecall reverts. A "non-reverting" ERC20 (e.g., one that returns `false` on insufficient balance/allowance instead of reverting, mirroring the ZRX-style behavior described in the reference report) will make the `transfer`/`transfer_from` CosmWasm messages complete successfully — emitting `action`, `from`, `to`, `amount` attributes as if the transfer happened — while no value was actually moved on the EVM side.

### Impact Explanation
Any contract or off-chain integration that treats the CW20 wrapper's message success (or its emitted attributes/events) as proof that a payment was completed can be deceived into releasing funds, NFTs, or other assets without having actually received the wrapped ERC20 tokens. This mirrors exactly the impact class in the reference report: a party can present a non-reverting token, cause `transfer`/`transferFrom` to silently no-op, and still have the calling logic treat it as a successful payment — enabling theft of counterparty funds in any escrow/lending/marketplace flow built atop the CW20 wrapper.

### Likelihood Explanation
Reachable by any unprivileged CosmWasm user: an attacker only needs to (1) deploy or use an existing non-reverting ERC20 token, (2) have (or create) a CW20 wrapper contract pointing at that token, and (3) call `transfer`/`transfer_from` with an amount that the underlying ERC20 will reject via `false` return rather than revert. No special privileges, validator collusion, or governance action are required — this is a straightforward single-transaction exploit against the CW↔EVM pointer/wrapper bridge.

### Recommendation
In `example/cosmwasm/cw20/src/contract.rs`, do not fire-and-forget the ERC20 `transfer`/`transferFrom` call. Either:
- Query the underlying ERC20 balance before and after (or use `EvmMsg` with a reply and decode the returned `bool`) and fail the CosmWasm message if the ERC20 call returned `false`, or
- Decode the ABI return data in `CallEVM`/`HandleInternalEVMDelegateCall` for calls targeting the standard `transfer`/`transferFrom` selectors and treat a `false` result as an error, propagating failure back through the message handler chain.

### Proof of Concept
1. Deploy an ERC20 contract whose `transfer`/`transferFrom` functions return `false` on failure instead of reverting (e.g., insufficient balance/allowance).
2. Deploy (or reuse) the CW20 wrapper contract from `example/cosmwasm/cw20` pointing at this ERC20.
3. As the "payer," call `transfer_from`/`transfer` on the CW20 wrapper for more tokens than actually held/approved.
4. Observe that `HandleInternalEVMDelegateCall`/`CallEVM` returns success (no revert), and the wrapper's `Response` is emitted with `action`/`amount` attributes indicating a completed transfer, even though the wrapped ERC20's internal state is unchanged.
5. Any external contract relying on this CosmWasm message succeeding as confirmation of payment (e.g., releases collateral/goods) is defrauded.

Note: I could not fully verify how `CallEVM`'s `ReturnData` is consumed by all downstream integrations beyond the wrapper example contract shown here (it is explicitly marked `example/`), so real-world exposure depends on whether production contracts built on this pattern also skip return-value checks; this could not be confirmed with certainty given index coverage limits on third-party/production CW contracts using this wrapper pattern.

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

**File:** x/evm/keeper/evm.go (L112-119)
```go
	res, err := k.applyEVMMessage(ctx, evmMsg, stateDB, gp, false)
	if err != nil {
		return nil, err
	}
	k.consumeEvmGas(ctx, res.UsedGas)
	if res.Err != nil {
		return nil, res.Err
	}
```

**File:** x/evm/keeper/evm.go (L145-153)
```go
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
