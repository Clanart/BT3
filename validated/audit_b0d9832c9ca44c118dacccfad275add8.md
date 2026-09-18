### Title
`HandleERC721TransferPayload` builds an unsafe `transferFrom` call for CW→EVM ERC721 transfers instead of `safeTransferFrom` - ([File: x/evm/client/wasm/query.go])

### Summary
The wasm binding `erc721_transfer_payload` (exposed to any CosmWasm contract) always encodes an EVM ERC721 `transferFrom` call rather than `safeTransferFrom`, mirroring the reported bug class of using the unsafe transfer primitive where a safe one exists to protect against receiver contracts that cannot handle ERC-721 tokens.

### Finding Description
`HandleERC721TransferPayload` packs calldata for the ERC721 ABI method `transferFrom(from, to, tokenId)`, never `safeTransferFrom`: [1](#0-0) 

This handler is reachable from any CosmWasm contract through the `erc721_transfer_payload` wasm query binding: [2](#0-1) 

The intended consumer pattern is a CW721-style pointer contract that resolves the token owner and then issues a `DelegateCallEvm` using this exact unsafe payload to move an underlying EVM ERC721 token on behalf of a CosmWasm `transfer_nft` message, as shown in the reference implementation: [3](#0-2) 

Separately, the analogous EVM-side pointer contract `CW721ERC721Pointer.sol` also only overrides the unsafe `transferFrom` (it does not override `safeTransferFrom`, and the underlying transfer is routed through a CosmWasm `transfer_nft` execute message with no receiver-acceptance check): [4](#0-3) 

The `to` address in both flows comes from address-association lookup (`GetEVMAddress`/`AddrPrecompile.getSeiAddr`) and is not validated for ERC721-receiver compatibility before the transfer executes: [5](#0-4) 

### Impact Explanation
If the resolved EVM recipient of a cross-VM NFT transfer (via a CW contract using `erc721_transfer_payload`, or via `CW721ERC721Pointer.transferFrom`) is a smart contract that does not implement `onERC721Received` (or intentionally rejects ERC-721 tokens), the underlying ERC721/CW721 asset can become permanently locked at that address, since the unsafe `transferFrom` path performs no receiver check. This is a permanent loss/freezing of the transferred NFT for the original owner, matching the fund-freezing impact criterion.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a CosmWasm contract or a `CW721ERC721Pointer` transfer path to be used to move an NFT to an address whose EVM-associated address is a contract lacking `onERC721Received`, which is plausible whenever cross-VM transfers target contract-controlled destinations (e.g., another CW/EVM bridge contract, a vault, or a marketplace escrow) rather than EOAs. No malicious validator or privileged actor is required — any transaction sender or CW721 pointer user can trigger the unsafe transfer.

### Recommendation
Use `safeTransferFrom` (with an empty/optional data argument) instead of `transferFrom` when packing the ABI call in `HandleERC721TransferPayload`, and correspondingly have `CW721ERC721Pointer.sol`'s `transferFrom` invoke (or additionally expose/override) a safe transfer path that verifies the recipient can accept ERC-721 tokens (via `IERC721Receiver.onERC721Received`) before finalizing the underlying CW721 `transfer_nft` execution, consistent with the OpenZeppelin guidance cited in the analog report.

### Proof of Concept
1. Deploy (or use) a `CW721ERC721Pointer` for an existing CW721 collection, or a CosmWasm contract that calls the `erc721_transfer_payload` binding.
2. Associate a Sei address with an EVM contract address that intentionally reverts in `onERC721Received` (or simply has no fallback logic to move the NFT further), i.e., is not ERC-721-receiver aware.
3. Call `CW721ERC721Pointer.transferFrom(owner, thatContract, tokenId)` directly, or have the CW contract call `erc721_transfer_payload` targeting that address and execute the resulting calldata via `DelegateCallEvm`.
4. Observe the transfer succeeds using `transferFrom` semantics with no `onERC721Received` check, and the token is now owned by a contract with no way to move it back out, permanently freezing the asset.

### Citations

**File:** x/evm/client/wasm/query.go (L202-224)
```go
func (h *EVMQueryHandler) HandleERC721TransferPayload(ctx sdk.Context, from string, recipient string, tokenId string) ([]byte, error) {
	abi, err := cw721.Cw721MetaData.GetAbi()
	if err != nil {
		return nil, err
	}
	fromEvmAddr, found := h.k.GetEVMAddress(ctx, sdk.MustAccAddressFromBech32(from))
	if !found {
		return nil, types.NewAssociationMissingErr(from)
	}
	toEvmAddr, found := h.k.GetEVMAddress(ctx, sdk.MustAccAddressFromBech32(recipient))
	if !found {
		return nil, types.NewAssociationMissingErr(recipient)
	}
	t, ok := sdk.NewIntFromString(tokenId)
	if !ok {
		return nil, errors.New("invalid token ID for ERC721, must be a big Int")
	}
	bz, err := abi.Pack("transferFrom", fromEvmAddr, toEvmAddr, t.BigInt())
	if err != nil {
		return nil, err
	}
	res := bindings.ERCPayloadResponse{EncodedPayload: base64.StdEncoding.EncodeToString(bz)}
	return json.Marshal(res)
```

**File:** wasmbinding/queries.go (L167-169)
```go
	case evmbindings.ERC721TransferType:
		c := parsedQuery.ERC721TransferPayload
		return qp.evmHandler.HandleERC721TransferPayload(ctx, c.From, c.Recipient, c.TokenID)
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

**File:** contracts/src/CW721ERC721Pointer.sol (L160-169)
```text
    function transferFrom(address from, address to, uint256 tokenId) public override {
        if (to == address(0)) {
            revert ERC721InvalidReceiver(address(0));
        }
        require(from == ownerOf(tokenId), "`from` must be the owner");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory tId = _formatPayload("token_id", _doubleQuotes(Strings.toString(tokenId)));
        string memory req = _curlyBrace(_formatPayload("transfer_nft", _curlyBrace(_join(recipient, tId, ","))));
        _execute(bytes(req));
    }
```
