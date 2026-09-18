### Title
Use of `transferFrom()` instead of `safeTransferFrom()` when encoding ERC721 transfer payloads for CosmWasm-initiated transfers can permanently lock ERC721/CW721 pointer NFTs - (File: x/evm/client/wasm/query.go)

### Summary
`HandleERC721TransferPayload` in [1](#0-0)  encodes an EVM call payload using the `transferFrom` selector rather than `safeTransferFrom`. This payload is exposed to CosmWasm contracts via the wasmbinding query interface, letting any CosmWasm message construct a raw ERC721 `transferFrom` calldata targeting an arbitrary EVM recipient address, with no verification that the recipient can handle ERC721 tokens.

### Finding Description
`HandleERC721TransferPayload` packs the ABI call as `abi.Pack("transferFrom", fromEvmAddr, toEvmAddr, t.BigInt())` [2](#0-1) , mirroring exactly the anti-pattern described in the external report: EIP-721 explicitly warns that `transferFrom()` places the burden of verifying the receiver's capability to hold NFTs entirely on the caller, whereas `safeTransferFrom()` performs an `onERC721Received` callback check and reverts if the target contract cannot handle the token.

This payload-building function is exposed through the wasm query bindings so that any CosmWasm contract can request an encoded transfer payload for a token it owns and then execute it via the EVM/wasmd bridge (`bindings.ERCPayloadResponse` is returned for the caller to submit as the payload of an EVM call) [3](#0-2) . Because the recipient is an arbitrary EVM address resolved only via `GetEVMAddress` association lookup (no code-length or `IERC721Receiver` interface check) [4](#0-3) , a CosmWasm user can trigger a transfer of a CW721-pointer-backed ERC721 (or any pointer-based ERC721) to a smart-contract address that does not implement `onERC721Received`, permanently locking the NFT since there is no recovery path for tokens sent to a non-compliant contract with `transferFrom`.

This differs from the CosmWasm-side `CW721TransferPayload` used for native CW721->CW721 transfers [5](#0-4) , which stays within the CosmWasm asset model (no ERC721 "receiver" callback risk); the vulnerable path is specific to the ERC721-facing `HandleERC721TransferPayload` helper that produces an EVM `transferFrom` calldata blob for cross-VM interoperability.

Note: the pointer contract itself, `CW721ERC721Pointer.sol`, overrides `transferFrom()` [6](#0-5)  but does not override `safeTransferFrom()`; since Solidity dispatches virtual calls dynamically, the OpenZeppelin base `safeTransferFrom()` still calls into the pointer's overridden `transferFrom()` and then performs the receiver check — so calling `safeTransferFrom()` on the pointer itself is safe. The actual unguarded path is the wasm-bindings payload builder in `x/evm/client/wasm/query.go`, which hard-codes the `transferFrom` selector and offers no `safeTransferFrom`-equivalent payload builder for CosmWasm callers.

### Impact Explanation
A CosmWasm contract/user relying on this bindings query to move an ERC721 token to a recipient contract (e.g., a marketplace, vault, or other smart contract integration) that lacks `onERC721Received` support will have the NFT permanently and irrecoverably locked in that contract, since `transferFrom` performs no receiver-capability check and there is no way to reclaim tokens transferred via `transferFrom` to a non-ERC721Receiver contract. This constitutes permanent fund (NFT) loss for the sender.

### Likelihood Explanation
Likelihood is moderate: it requires a CosmWasm contract author or user to request the "transfer payload" via wasm bindings query and use it to send a pointer/ERC721 token to a smart-contract recipient that is not `IERC721Receiver`-compliant, a scenario plausible for any tokenized cross-VM integration (e.g., marketplaces, escrow, vault contracts) built atop the ERC721/CW721 pointer bridge.

### Recommendation
Change `HandleERC721TransferPayload` to pack the `safeTransferFrom(address,address,uint256)` selector (or add an explicit `safeTransferFrom` variant and deprecate/guard the unsafe one), so cross-VM ERC721 transfers initiated from CosmWasm perform the standard receiver-capability check before completing the transfer.

### Proof of Concept
1. A CosmWasm contract calls the wasm binding query that resolves to `EVMQueryHandler.HandleERC721TransferPayload(ctx, fromSeiAddr, toSeiAddr, tokenId)` [1](#0-0) , where `toSeiAddr` resolves (via `GetEVMAddress`) to an EVM address of a deployed smart contract that does not implement `onERC721Received`.
2. The handler returns ABI-encoded `transferFrom(from, to, tokenId)` calldata with no receiver check.
3. The CosmWasm contract submits this payload as an EVM message/execute call against the pointer or ERC721 contract; the transfer succeeds because `transferFrom()` (unlike `safeTransferFrom()`) does not call `onERC721Received`.
4. The NFT is now owned by a contract with no logic to transfer it back out, permanently locking the asset.

### Citations

**File:** x/evm/client/wasm/query.go (L202-225)
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
}
```

**File:** precompiles/solo/legacy/v65/solo.go (L375-387)
```go
func CW721TransferPayload(recipient sdk.AccAddress, token string) []byte {
	type request struct {
		Recipient string `json:"recipient"`
		Token     string `json:"token_id"`
	}
	raw := request{Recipient: recipient.String(), Token: token}
	bz, err := json.Marshal(map[string]interface{}{"transfer_nft": raw})
	if err != nil {
		// should be impossible
		panic(err)
	}
	return bz
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
