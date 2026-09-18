Confirmed: `HandleInternalEVMDelegateCall` at [1](#0-0)  calls `k.CallEVM(ctx, senderEvmAddr, to, &zeroInt, req.Data)` where `senderEvmAddr` is derived from `req.Sender` — the **original transaction signer's** EVM address (`FromContract` is only used to verify the pointer relationship, not as the "from" of the call). So the underlying `transferFrom` executes with `msg.sender = original tx signer's EVM address`, not the pointer contract's own address.

### Title
Unauthorized CW721-pointer NFT transfer via missing owner/approval check before `transferFrom` delegatecall - ([File: example/cosmwasm/cw721/src/contract.rs])

### Summary
The `TransferNft`/`SendNft` execute handlers of the reference CW721-to-ERC721 pointer contract build a `transferFrom(owner, recipient, token_id)` payload using the token's actual on-chain `owner` — obtained via an unauthenticated ownership lookup — instead of verifying that `info.sender` (the wasm message sender) is that owner or an approved operator/spender for the specific `token_id`.

### Finding Description
`transfer_nft` in [2](#0-1)  does:
```
let owner = querier.erc721_owner(info.sender.to_string(), erc_addr.to_string(), token_id.to_string())?.owner;
let payload = querier.erc721_transfer_payload(owner, recipient.to_string(), token_id.to_string())?;
```
`info.sender` here is passed only as the "caller" context for the static EVM call in `HandleERC721Owner` [3](#0-2) , which simply returns `ownerOf(token_id)` — the *actual* token owner, irrespective of who is asking. There is no comparison of `info.sender` against `owner`, nor any check of ERC-721 `getApproved`/`isApprovedForAll` for `info.sender`, in the Rust contract before it issues the transfer instruction. `execute_transfer_nft` and `execute_send_nft` ( [4](#0-3) ) call this unguarded `transfer_nft` directly.

The resulting `EvmMsg::DelegateCallEvm{ to: erc_addr, data: transferFrom(owner, recipient, token_id) }` is turned into a `MsgInternalEVMDelegateCall` by `EncodeDelegateCallEVM` [5](#0-4) , whose `Sender` field is the *original wasm message sender* (`info.Sender`), not the pointer contract. `HandleInternalEVMDelegateCall` [6](#0-5)  then calls the underlying ERC721 contract's `transferFrom` with `from = senderEvmAddr` (the associated EVM address of the original caller). Since the payload's `from` argument is the real `owner`, not the caller, the ERC721 contract's own `_isApprovedOrOwner(from, msg.sender, id)` check (see e.g. [7](#0-6) ) compares `owner == msg.sender` where `msg.sender` is the *caller* address (since this is a top-level EVM call from `senderEvmAddr`, not a delegatecall at the EVM level) — meaning `_isApprovedOrOwner` only passes if the caller genuinely is the owner or the owner has approved the caller. This EVM-side check is the only backstop; the CosmWasm pointer layer itself performs **no authorization check** of its own.

### Impact Explanation
Because the pointer-contract layer omits the sender/owner check entirely, the security guarantee that "only the token owner (or an approved spender) can transfer the token" depends solely on the underlying EVM `transferFrom`'s `_isApprovedOrOwner` check being evaluated with the correct `msg.sender`. Any drift between the two layers (e.g., pointer contracts targeting ERC721 implementations with different approval semantics, ERC721 tokens that trust `SafeTransferFrom` callbacks differently, or any implementation where `owner`/approval state diverges from what the wasm layer assumes) can result in unauthorized transfer of another user's NFT with no defense-in-depth at the CosmWasm layer. This matches the reachable bug class: an unprivileged wasm message sender can attempt to move NFTs they neither own nor are approved for by simply naming an arbitrary `token_id`, relying entirely on a single check at a different layer instead of two independent, defense-in-depth authorization checks.

### Likelihood Explanation
This code path is reachable by any CosmWasm user executing `transfer_nft`/`send_nft` against a deployed CW721↔ERC721 pointer contract with an arbitrary `token_id`, requiring no special privilege beyond ability to submit a `MsgExecuteContract`. Exploitation of actual fund loss requires the EVM-side `_isApprovedOrOwner` check to be bypassed or not exist for the specific underlying ERC721 implementation being pointed to, which is not proven for the reference `ERC721.sol` shown here — the reference implementation does enforce it correctly. The vulnerability is therefore a missing defense-in-depth authorization check at the CosmWasm layer of the reference pointer contract, and its concrete exploitability for fund loss depends on the specific target ERC721 contract's transfer authorization implementation.

### Recommendation
Add an explicit authorization check in `transfer_nft` (and equivalently in the CW1155 pointer's send/send_batch logic) before constructing the transfer payload: verify `info.sender == owner`, or query `erc721_approved`/`erc721_is_approved_for_all` for `info.sender`, and reject with a contract-level error if unauthorized — mirroring the reference `ERC721.sol`'s `_isApprovedOrOwner` logic — so the CosmWasm pointer layer does not rely solely on the underlying EVM contract for this critical check.

### Proof of Concept
Not independently verified end-to-end in this review; the code path was traced statically. A concrete PoC would require deploying the example CW721 pointer over an ERC721 implementation whose `transferFrom` does not strictly enforce `_isApprovedOrOwner` (or exploiting any inconsistency between the wasm-layer `owner` lookup and the EVM-layer authorization check), then calling `transfer_nft` for a `token_id` owned by another account from an unauthorized signer's account and observing the transfer succeed. I was unable to fully confirm whether any of the pointer/ERC721 example implementations bundled in this repo have such a discrepancy, since the reference `ERC721.sol` enforces authorization correctly at the EVM layer, making the missing wasm-layer check a latent defense-in-depth gap rather than a demonstrated, currently-exploitable fund-loss bug in the exact code paths reviewed.

### Citations

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

**File:** example/cosmwasm/cw721/src/contract.rs (L70-104)
```rust
pub fn execute_transfer_nft(
    deps: DepsMut<EvmQueryWrapper>,
    info: MessageInfo,
    recipient: String,
    token_id: String,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer_nft(deps, &info, &recipient, &token_id)?;
    res = res.add_attribute("action", "transfer_nft")
        .add_attribute("sender", info.sender)
        .add_attribute("recipient", recipient)
        .add_attribute("token_id", token_id);
    Ok(res)
}

pub fn execute_send_nft(
    deps: DepsMut<EvmQueryWrapper>,
    info: MessageInfo,
    recipient: String,
    token_id: String,
    msg: Binary,
) -> Result<Response<EvmMsg>, ContractError> {
    let mut res = transfer_nft(deps, &info, &recipient, &token_id)?;
    let send = Cw721ReceiveMsg {
        sender: info.sender.to_string(),
        token_id: token_id.to_string(),
        msg,
    };
    res = res
        .add_message(send.into_cosmos_msg(recipient.clone())?)
        .add_attribute("action", "send_nft")
        .add_attribute("sender", info.sender)
        .add_attribute("recipient", recipient)
        .add_attribute("token_id", token_id);
    Ok(res)
}
```

**File:** example/cosmwasm/cw721/src/contract.rs (L175-192)
```rust
fn transfer_nft(
    deps: DepsMut<EvmQueryWrapper>,
    info: &MessageInfo,
    recipient: &str,
    token_id: &str,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(recipient)?;

    let erc_addr = ERC721_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let owner = querier.erc721_owner(info.sender.to_string(), erc_addr.to_string(), token_id.to_string())?.owner;
    let payload = querier.erc721_transfer_payload(owner, recipient.to_string(), token_id.to_string())?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new().add_message(msg);

    Ok(res)
}
```

**File:** x/evm/client/wasm/query.go (L167-200)
```go
func (h *EVMQueryHandler) HandleERC721Owner(ctx sdk.Context, caller string, contractAddress string, tokenId string) ([]byte, error) {
	callerAddr, err := sdk.AccAddressFromBech32(caller)
	if err != nil {
		return nil, err
	}
	contract := common.HexToAddress(contractAddress)
	abi, err := cw721.Cw721MetaData.GetAbi()
	if err != nil {
		return nil, err
	}
	t, ok := sdk.NewIntFromString(tokenId)
	if !ok {
		return nil, errors.New("invalid token ID for ERC721, must be a big Int")
	}
	bz, err := abi.Pack("ownerOf", t.BigInt())
	if err != nil {
		return nil, err
	}
	res, err := h.k.StaticCallEVM(ctx, callerAddr, &contract, bz)
	if err != nil {
		return nil, err
	}
	typed, err := abi.Unpack("ownerOf", res)
	if err != nil {
		return nil, err
	}
	typedOwner := typed[0].(common.Address)
	owner := ""
	if (typedOwner != common.Address{}) {
		owner = h.k.GetSeiAddressOrDefault(ctx, typedOwner).String()
	}
	response := bindings.ERC721OwnerResponse{Owner: owner}
	return json.Marshal(response)
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

**File:** contracts/src/ERC721.sol (L111-119)
```text
    function _isApprovedOrOwner(
        address owner,
        address spender,
        uint id
    ) internal view returns (bool) {
        return (spender == owner ||
        isApprovedForAll[owner][spender] ||
            spender == _approvals[id]);
    }
```
