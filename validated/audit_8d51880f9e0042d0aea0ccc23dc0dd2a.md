### Title
CW20-wrapper for ERC20 tokens spoofs successful transfers when the wrapped ERC20 returns `false` instead of reverting - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
The CW↔EVM "CW wrapper for ERC tokens" mechanism lets any CW20 wrapper be instantiated over an arbitrary, caller-chosen ERC20 token address, and forwards `transfer`/`transfer_from` calls to that ERC20 via a `DelegateCallEvm` sub-message. Neither the wrapper contract nor the underlying EVM delegate-call plumbing inspects the ABI-encoded boolean return value of the wrapped ERC20's `transfer`/`transferFrom`; only a VM-level revert is treated as failure. This is exactly the "non-reverting-on-failure ERC20" bug class from the referenced report, reproduced at the Sei CW↔EVM pointer/bridge layer instead of inside `UXDController`.

### Finding Description
The CW20 wrapper's `transfer` and `transfer_from` handlers build an EVM payload for the wrapped ERC20 and hand it off as a Cosmos sub-message without ever checking the returned `bool`: [1](#0-0) [2](#0-1) 

The message is a `EvmMsg::DelegateCallEvm`, which is routed through the wasm bridge encoder to `MsgInternalEVMDelegateCall`: [3](#0-2) 

That message is processed by `HandleInternalEVMDelegateCall`, which calls `CallEVM` and only surfaces an error if the EVM execution itself reverted (`res.Err`); it never decodes/validates the ABI return data (the `bool` that `transfer`/`transferFrom` are supposed to return): [4](#0-3) [5](#0-4) 

Because a non-reverting-on-failure ERC20 simply returns `false` on a failed `transfer`/`transferFrom` (no revert), `res.Err` stays `nil`, `CallEVM` returns success, and the CosmWasm `Response` built by the wrapper (with `from`/`to`/`amount` attributes, and for `Cw20ReceiveMsg` flows, a hook to the recipient contract) is emitted as if the transfer genuinely happened — even though no value moved on the EVM side.

### Impact Explanation
Any contract or off-chain system that trusts the wrapper's success response/attributes/hook (e.g., a receiving contract implementing `Cw20ReceiveMsg`, a DEX or vault crediting a deposit, or an indexer) can be made to believe funds were transferred when they were not. An attacker can wrap a non-reverting ERC20 (or, more actionable, one they fully control) into a CW20 wrapper, then call `transfer`/`transfer_from` toward a victim contract that credits balances based on the wrapper's reported success, extracting value without ever actually transferring the underlying ERC20 — a direct fund-loss/unauthorized-transfer vector via the pointer/wasm bridge, consistent with the "Accept only concrete fund loss ... unauthorized transfer via precompile or pointer" validation criterion.

### Likelihood Explanation
Any unprivileged user can instantiate a CW20 wrapper (or use an existing one) over any ERC20 address, including a token deliberately crafted to return `false` on failure rather than revert, and then invoke `transfer`/`transfer_from` toward a contract that trusts the reported outcome. No special privilege or validator collusion is required — a single crafted transaction/wasm message triggers the flaw, and both the wrapper contract and the underlying `CallEVM`/`HandleInternalEVMDelegateCall` code path are production logic that ships with the chain.

### Recommendation
After issuing the `DelegateCallEvm` sub-message for `transfer`/`transfer_from`/`approve`, decode and check the returned boolean (or otherwise require the underlying ERC20 to conform to a strict "revert on failure" ABI) before returning a success `Response`. Alternatively, harden `CallEVM`/`HandleInternalEVMDelegateCall` (or provide a wrapper-side helper analogous to OpenZeppelin's `SafeERC20`) to reject calls whose return data indicates `false`, so pointer/wrapper contracts cannot report success for a no-op ERC20 transfer.

### Proof of Concept
1. Deploy a malicious/non-standard ERC20 (`EvilToken`) whose `transfer`/`transferFrom` never revert on insufficient balance/allowance and simply `return false`.
2. Instantiate the CW20 wrapper contract (`example/cosmwasm/cw20`) with `ERC20_ADDRESS` set to `EvilToken`.
3. From an account with zero `EvilToken` balance, call the wrapper's `transfer` (or `transfer_from`) targeting a victim contract that implements `Cw20ReceiveMsg` and credits balances/executes logic based on the reported amount.
4. The wrapper's `transfer` handler builds and sends the `DelegateCallEvm` payload; `EvilToken.transfer` returns `false` but does not revert, so `HandleInternalEVMDelegateCall`/`CallEVM` return success (`res.Err == nil`).
5. The wrapper emits a successful `Response` with `from`/`to`/`amount` attributes even though `EvilToken`'s balance of the sender is unchanged — the victim contract is deceived into treating the transfer as real.

Note: this analysis relies on `example/cosmwasm/cw20` as the reference/production implementation for the "CW wrapper for ERC tokens" pointer feature described in `x/evm/AGENTS.md`; I was not able to fully confirm (within the available search budget) whether a separately embedded, chain-deployed bytecode artifact for this specific wrapper direction exists analogous to `x/evm/artifacts/cw20` for the reverse (CW20→ERC20) pointer. If such a distinct production artifact exists and differs from this example contract, it should be checked for the same missing-return-value-check pattern.

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
