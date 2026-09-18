### Title
CW20-over-ERC20 pointer contract does not check the ERC20 `transfer`/`transferFrom` boolean return value, allowing accounting to diverge from actual token movement - (File: example/cosmwasm/cw20/src/contract.rs)

### Summary
The example CosmWasm CW20 pointer contract that wraps an arbitrary ERC20 token (`ERC20_ADDRESS`) builds a `DelegateCallEvm` message to invoke the underlying ERC20's `transfer`/`transferFrom` functions, but never inspects the ABI-encoded `bool` return value of those calls. Only the EVM-call success/failure (i.e. whether it reverted) is checked downstream; a non-compliant ERC20 that returns `false` instead of reverting on failed transfer will be treated as a fully successful transfer by the wrapping CW20 contract, exactly the "not using safeERC20" class of bug from the reference report.

### Finding Description
`transfer` and `transfer_from` in the example CW20 pointer contract construct a `DelegateCallEvm` message carrying the ABI-encoded `transfer`/`transferFrom` payload for the wrapped ERC20 token, and simply attach it as a `CosmosMsg`: [1](#0-0) [2](#0-1) 

Neither function decodes or validates the `bool` return value that a standard ERC20 `transfer`/`transferFrom` call is supposed to produce; they simply add the message to the response and report the transfer attributes ("from", "to", "amount") as if the transfer definitively occurred.

On the EVM/Cosmos SDK side, the message is routed to `HandleInternalEVMDelegateCall`, which calls `CallEVM` and only fails if the EVM execution itself reverted (`res.Err != nil`); the raw `res.ReturnData` (which would contain the ERC20's boolean return value) is passed back but is never decoded or checked by the CosmWasm message handling path: [3](#0-2) [4](#0-3) 

Because CosmWasm's `CosmosMsg` execution model treats a message as successful as long as the corresponding `sdk.Msg` handler returns no error, an ERC20 implementation that returns `false` on failure (rather than reverting — a common pattern for legacy/non-compliant tokens such as some USDT-style implementations) will cause the delegate call to succeed at the EVM layer while the actual underlying token transfer failed. The CW20 wrapper contract has no way to detect this and will emit success attributes/events despite no tokens actually moving.

This mirrors the OZ `SafeERC20`-class issue in the original report: the caller relies on `require(success)`-style checking of only the call's revert status, not the ERC20 method's boolean return, and thus can silently under-account or over-report successful transfers when interacting with a non-standard ERC20 token.

### Impact Explanation
If the pointer/wrapping CW20 contract is registered against any ERC20 token whose `transfer`/`transferFrom` can return `false` without reverting, users can be told (via successful transaction attributes/response) that a transfer succeeded when the underlying ERC20 balance did not change. This can lead to fund-accounting mismatches between the ERC20 side and the CW20 side of the pointer, effectively allowing loss of funds/wrong-crediting for the receiving party relying on the CW20 message's success semantics — a concrete fund-loss/mismatch scenario, satisfying the Medium-severity bar for unauthorized/incorrect transfer accounting via the CW<->EVM pointer bridge.

### Likelihood Explanation
Likelihood depends on the specific ERC20 token being wrapped: standard OZ-based ERC20s always revert on failure so this is not exploitable against them, but the codebase explicitly supports pointing to arbitrary user-deployed ERC20 tokens via the CW20 pointer flow, so any token author (or malicious token deployer) can create a non-reverting ERC20 and register/interact with a CW20 wrapper pointing at it, deterministically triggering the mismatch on every failed transfer.

### Recommendation
When constructing the `DelegateCallEvm`/`MsgInternalEVMDelegateCall` transfer messages in `example/cosmwasm/cw20/src/contract.rs`, decode the returned data as the ABI-encoded `bool` from `res.ReturnData` (or have `HandleInternalEVMDelegateCall`/`CallEVM` surface this data explicitly) and require it to be `true` before treating the transfer as successful, mirroring OZ's `SafeERC20.safeTransfer`/`safeTransferFrom` pattern used elsewhere in the repo (e.g. `TransferHelper.sol` and `UniswapV2Pair._safeTransfer`).

### Proof of Concept
1. Deploy a custom ERC20 contract whose `transfer`/`transferFrom` returns `false` on insufficient balance instead of reverting.
2. Register a CW20 pointer for this ERC20 (`ERC20_ADDRESS` stored in the CW20 contract).
3. Call the CW20 contract's `transfer` (or `transfer_from`) execute message for an amount exceeding the sender's actual ERC20 balance.
4. Observe that the `DelegateCallEvm` message succeeds (no revert, since the ERC20 call itself does not revert, just returns `false`), causing `HandleInternalEVMDelegateCall`/`CallEVM` to return without error, and the CW20 contract's transaction succeeds with "from"/"to"/"amount" attributes recorded — even though the wrapped ERC20 balance never changed.

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
