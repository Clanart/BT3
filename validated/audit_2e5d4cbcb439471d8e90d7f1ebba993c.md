This is important to note: the artifacts under `x/evm/artifacts/erc20`, `erc721`, `erc1155` (i.e., `x/evm/artifacts/cw20`, `cw721`, `cw1155`) are the actual production **CW → ERC wrapper bytecode** deployed on-chain via `AddCW20`/`AddCW721`/`AddCW1155` pointer registration, per `x/evm/migrations/store_cw_pointer_code.go` and `precompiles/pointer/*/pointer.go`. The `example/cosmwasm/cw20`, `cw721`, `cw1155` source trees are the reference Rust source for these wasm artifacts (the "CW wrappers for ERC tokens" described in `x/evm/AGENTS.md`). I was not able to fully confirm this mapping (i.e., that the exact compiled bytecode of `example/cosmwasm/cw20` is what's embedded as `x/evm/artifacts/cw20`) within the remaining tool budget — this should be verified in a Devin session by diffing the wasm build output against the embedded artifact.

### Title
CW20/CW721/CW1155 wrapper contracts don't check the boolean return value of the underlying ERC-token `transfer`/`transferFrom` delegatecall, allowing silent transfer failures - (File: `example/cosmwasm/cw20/src/contract.rs`)

### Summary
Sei's CW↔EVM pointer/wrapper system lets a CosmWasm contract represent (wrap) an arbitrary ERC20/ERC721/ERC1155 token so it can be moved through CW20/CW721/CW1155-style `execute` messages. The wrapper's `transfer`/`transfer_from`/`send` handlers build an ABI-encoded call to the underlying ERC token's `transfer`/`transferFrom` function and dispatch it via `EvmMsg::DelegateCallEvm`, but never inspect the boolean success value that `transfer`/`transferFrom` return. This mirrors the Sherlock Union Finance M-1 finding: using `transfer()`/`transferFrom()` without checking the returned boolean allows non-compliant/"weird" ERC20 tokens (tokens that return `false` on failure rather than reverting) to silently fail while the wrapper reports success.

### Finding Description
`example/cosmwasm/cw20/src/contract.rs::transfer` and `transfer_from` build a delegatecall payload and unconditionally add it as a `CosmosMsg`, attaching `Transfer`/attribute events and returning `Ok(res)` regardless of what the target ERC20's `transfer`/`transferFrom` actually returns: [1](#0-0) [2](#0-1) 

The `DelegateCallEvm` message is routed to `MsgInternalEVMDelegateCall`, handled in `x/evm/keeper/evm.go::HandleInternalEVMDelegateCall`, which only errors (reverting the whole tx) if the low-level EVM call itself fails (revert/out-of-gas/etc.) — it does not decode and check the ABI return value of the call it makes: [3](#0-2) 

Contrast this with the pointer contracts written for the *opposite* direction (ERC→CW20, `CW20ERC20Pointer.sol`) which correctly gate on the raw `delegatecall` success bit via `require(success, "CosmWasm execute failed")` — but that check only guarantees the CosmWasm side didn't error; it says nothing about the token-transfer boolean either, since `_execute()` never decodes/validates a returned success flag beyond the outer call succeeding: [4](#0-3) [5](#0-4) 

The pattern matches the Sherlock report exactly: an ERC20 `transfer`/`transferFrom` call is made and only the "did the call revert" bit is checked, not "did the token report success". A non-standard ERC20 (e.g., a token that returns `false` on insufficient balance instead of reverting — one of the documented [weird-erc20](https://github.com/d-xo/weird-erc20) behaviors that Sherlock explicitly ruled in-scope when the token type is named in the readme) would let `HandleInternalEVMDelegateCall` return successfully (no revert), causing the CW20 wrapper to emit `Transfer` attributes and return `Ok` even though no balance actually moved on the EVM side.

### Impact Explanation
If the wrapped ERC20/ERC721/ERC1155 token is non-compliant (returns `false` instead of reverting on failed transfer, e.g., due to insufficient balance/allowance or blacklist logic), the CW20 wrapper contract will believe the transfer succeeded (no error propagated, `Transfer` event/attributes emitted) while the actual token balance never moved. Any downstream Cosmos-side logic, off-chain indexers, or composed CosmWasm contracts trusting the CW wrapper's emitted events/state would record an inconsistent balance versus the real EVM-side ERC20 state — a form of accounting desync that can be leveraged for double-spend-like behavior (e.g., relying on the wrapper's optimistic success to trigger a further payout/mint while the real transfer silently failed). This satisfies the "unauthorized transfer via precompile or pointer" / fund-loss criteria.

### Likelihood Explanation
Likelihood is bounded by the requirement that the wrapped token itself must be non-standard (return `false` rather than revert). Because Sei explicitly supports "any ERC20 complying with the standard" as pointer targets and does not appear to restrict wrapping to a whitelist of known-compliant tokens, any user can create a CW1155/CW721/CW20 wrapper pointer for an externally-deployed, attacker-chosen or third-party non-standard ERC20/ERC721/ERC1155 token and then invoke `transfer`/`transfer_from` on the wrapper — a fully unprivileged, single-transaction path (`MsgExecuteContract` → `DelegateCallEvm` → `MsgInternalEVMDelegateCall`).

### Recommendation
In the CW wrapper contracts (`example/cosmwasm/cw20/src/contract.rs`, `cw721/src/contract.rs`, `cw1155/src/contract.rs`, and any compiled/embedded artifact derived from them), have `HandleInternalEVMDelegateCall` (or the wrapper's query/payload layer) decode the ABI return data of the delegatecall and require it to equal `true`/a successful response before emitting success attributes, mirroring an OpenZeppelin-`SafeERC20`-style check. Alternatively, restrict delegatecall targets for pointer creation to tokens verified to strictly conform to the ERC20/721/1155 return-value contract, or explicitly document that non-compliant tokens are unsupported for pointer wrapping (removing the medium-severity impact by scoping it out, as Sherlock does when weird tokens aren't declared in-scope).

### Proof of Concept
1. Deploy a non-standard ERC20 contract on the Sei EVM whose `transfer`/`transferFrom` return `false` on failure instead of reverting (e.g., insufficient balance path returns `false`).
2. Register a CW20 pointer/wrapper for this ERC20 (`AddCW20`/register-cw-pointer flow, `precompiles/pointer/*/pointer.go`).
3. Call the CW20 wrapper's `transfer` (or `transfer_from`) with an amount exceeding the sender's real ERC20 balance (or otherwise a call the token would refuse without reverting).
4. Observe: `HandleInternalEVMDelegateCall` returns no error (`x/evm/keeper/evm.go` lines 47–77) because the underlying EVM call did not revert; the CW20 wrapper's `execute_transfer` returns `Ok(res)` with `Transfer` attributes (`example/cosmwasm/cw20/src/contract.rs` lines 227–248), even though the ERC20's actual `balanceOf` for sender/recipient is unchanged.
5. This confirms the wrapper's silent-failure behavior mirrors the Sherlock M-1 finding.

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

**File:** contracts/src/CW20ERC20Pointer.sol (L79-96)
```text
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
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
