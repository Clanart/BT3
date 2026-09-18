### Title
CW20 wrapper for non-standard ERC20 tokens can report successful transfers without any value moving - ([File: x/evm/keeper/evm.go])

### Summary
`HandleInternalEVMDelegateCall`, which backs the CW20-wraps-ERC20 pointer contract's `transfer`/`transfer_from` execution path, only checks whether the underlying EVM call reverted. It never inspects the ABI-decoded boolean return value of the delegatecalled `transfer`/`transferFrom` selector. Non-standard ERC20 tokens that return `false` on failure instead of reverting (e.g. tokens like Lido's stETH-style transferFrom) will therefore cause the CW20 wrapper to treat a failed token movement as a success.

### Finding Description
The CW20 wrapper contract for ERC20 tokens (`example/cosmwasm/cw20/src/contract.rs`) implements `transfer`/`transfer_from` by fetching an ABI-encoded ERC20 `transfer`/`transferFrom` payload and dispatching it as `EvmMsg::DelegateCallEvm`: [1](#0-0) [2](#0-1) 

That custom message is handled on the Go side by `HandleInternalEVMDelegateCall`, which forwards to `CallEVM` and only propagates an `error`: [3](#0-2) 

`CallEVM` returns an error only when the EVM execution reverts (out of gas, explicit `revert`, etc.). A standards-compliant ERC20 that reverts on insufficient balance/allowance is safe here. But a non-standard ERC20 that returns the boolean `false` instead of reverting (the exact bug class described in the external report for `UXDController.sol`) will make the delegatecall succeed at the EVM level with `ret` encoding `false`. `HandleInternalEVMDelegateCall` discards `ret` entirely and returns `nil` error, so the wasm `execute()` call succeeds and the CW20 wrapper's `Response` is committed as if the transfer happened — while no balance actually moved on the wrapped ERC20 contract.

This is architecturally the CW-side counterpart of the "CW wrappers for ERC tokens" mechanism documented for pointer contracts: [4](#0-3) 

By contrast, the ERC20-side pointer for CW20 tokens (`contracts/src/CW20ERC20Pointer.sol`) is safe because its `_execute` helper `require(success, ...)`s on the delegatecall itself, which reverts the EVM transaction if the CosmWasm execute message errors: [5](#0-4) 
No equivalent check on the *decoded return value* exists on the reverse (CW-wraps-ERC20) path, since the Go handler that would need to inspect it discards the returned bytes.

### Impact Explanation
Any CosmWasm contract that composes with a CW20-wrapped ERC20 (e.g. a DEX, escrow, lending market, or any contract that calls `transfer`/`transfer_from` on the wrapper and trusts a non-erroring `Response` as proof of payment) can be tricked into believing a payment/transfer succeeded when the underlying non-standard ERC20 silently failed. This is a fund-loss / accounting-desync vector: the caller/attacker keeps the "collateral" while the downstream CW logic proceeds as though it received the tokens, matching the "unauthorized transfer via precompile or pointer" / fund-loss class in scope.

### Likelihood Explanation
Reachable by any CosmWasm message sender using a CW20-wraps-ERC20 pointer that targets a non-standard ERC20 token (tokens with non-reverting transfer/transferFrom on failure exist in the wild, e.g. some liquid-staking tokens). No privileged access is required — only a standard `MsgExecuteContract` invoking `transfer`/`transfer_from` against such a wrapped token, and a downstream consumer contract that doesn't independently re-check balances.

### Recommendation
In `HandleInternalEVMDelegateCall` (and generally wherever `CallEVM`/`CallEVM`-derived delegatecalls invoke ERC20 `transfer`/`transferFrom` on behalf of CW20 wrapper logic), decode the returned bytes and treat a `false` boolean result (or a return that fails ABI-decoding as `bool` when non-empty) as an error, mirroring `require(success && (data.length == 0 || abi.decode(data, (bool))))` patterns used elsewhere in the codebase (e.g. `TransferHelper.sol`). Alternatively/additionally, have the CW20 wrapper contract explicitly compare `balanceOf` before and after the delegatecall and fail the execution if the expected delta isn't observed.

### Proof of Concept
1. Deploy (or use) a non-standard ERC20 whose `transfer`/`transferFrom` returns `false` on insufficient balance/allowance instead of reverting.
2. Register/instantiate the CW20 wrapper (`example/cosmwasm/cw20`, `ERC20_ADDRESS` pointing at the non-standard token) via `instantiate`.
3. As an unprivileged account with zero balance/allowance on the underlying ERC20, submit `MsgExecuteContract` with `ExecuteMsg::Transfer{ recipient, amount }` (or `TransferFrom`) for a nonzero `amount`.
4. The wasm `execute` call for the CW20 wrapper's `EvmMsg::DelegateCallEvm` resolves through `HandleInternalEVMDelegateCall` → `CallEVM`; since the underlying ERC20 call returns `false` without reverting, `err == nil` and the wrapper's `Response` (with `from`/`to`/`amount` attributes) is committed successfully, even though `balanceOf(sender)`/`balanceOf(recipient)` on the underlying ERC20 are unchanged.
5. Any consuming CW contract observing this successful execution (or its emitted attributes/events) as proof of payment can be exploited to release value without ever receiving real tokens.

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

**File:** x/evm/AGENTS.md (L106-119)
```markdown
**EVM pointers for CW/native tokens:**
- Native Cosmos denoms get an ERC20 representation.
- CW20 tokens get an ERC20 pointer.
- CW721 NFTs get an ERC721 pointer.
- CW1155 multi-tokens get an ERC1155 pointer.

**CW wrappers for ERC tokens:**
- ERC20 tokens get a CW20 wrapper.
- ERC721 NFTs get a CW721 wrapper.
- ERC1155 multi-tokens get a CW1155 wrapper.

Pointers are versioned and can be upgraded. A reverse registry allows looking up the original token from its pointer address.

Pre-compiled bytecode for all pointer contracts is embedded in the binary under `artifacts/`.
```

**File:** contracts/src/CW20ERC20Pointer.sol (L98-109)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw20Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```
