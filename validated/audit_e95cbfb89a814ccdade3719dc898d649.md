### Title
CW pointer contracts can be registered with a mismatched ERC token-standard type, causing type confusion between ERC20/ERC721/ERC1155 semantics - (File: x/evm/keeper/msg_server.go, x/evm/types/message_register_pointer.go)

### Summary
`MsgRegisterPointer` lets any account create a CosmWasm pointer for an arbitrary EVM contract address, tagged with a self-declared `PointerType` (`ERC20`, `ERC721`, or `ERC1155`). Neither `ValidateBasic()` nor the `RegisterPointer` message handler verify that the target EVM contract actually implements the declared token standard before instantiating the pointer and persisting the mapping.

### Finding Description
`MsgRegisterPointer.ValidateBasic()` only checks that the sender address is valid and that `ErcAddress` is a well-formed hex address; it never checks the declared `PointerType` against the actual contract at that address: [1](#0-0) 

`RegisterPointer` then switches purely on the caller-supplied `msg.PointerType` to pick the CW code ID and instantiate payload (`erc20_address` / `erc721_address` / `erc1155_address`), and immediately persists the EVM<->CW mapping — with no call into the target contract to confirm it supports the corresponding interface (e.g. no `supportsInterface`/ERC-165 check, no probing of `ownerOf`/`balanceOf` semantics): [2](#0-1) [3](#0-2) 

This is the same root-cause pattern as the referenced Teller report: the protocol trusts a caller-supplied "type" tag for a token contract instead of verifying the actual token standard the contract implements. Because ERC20 and ERC721 share an identical function selector for `transferFrom(address,address,uint256)` (and `approve(address,uint256)`), an EVM contract that is really an ERC20 token can be registered with `PointerType_ERC721`, and vice versa. The resulting CW pointer (instantiated from the stored pointer wasm code with e.g. `erc721_address` set to an actual ERC20 contract) will subsequently generate/execute payloads assuming ERC721 semantics (treating the third `transferFrom` argument as a `tokenId`, calling `ownerOf(tokenId)`, etc.) against a contract that actually interprets that argument as an `amount`, and has no `ownerOf` function at all (which would simply revert, but any function that shares a selector such as `transferFrom`/`approve` will silently succeed with the wrong semantics).

### Impact Explanation
If a CW721/CW1155-style pointer is created against an actual ERC20 contract (or vice-versa) via `RegisterPointer`, all subsequent interactions through that pointer are semantically incorrect: `transferFrom`/`approve` calls sharing selectors with ERC20 will execute against the ERC20 contract's balance/allowance rather than a specific NFT `tokenId`, and read functions like `ownerOf`/`balanceOf` will decode ERC20 return data as ERC721 data or vice versa. This can allow moving/burning value that a caller did not intend to move (e.g. supplying "tokenId 2000" causes 2000 units of the ERC20 token to move), or bricking/miscounting balances for downstream consumers (marketplaces, lending contracts, wallets) that trust the pointer's declared standard. This matches the "fund loss via type confusion" impact class in the source report, mapped onto Sei's CW↔EVM pointer bridge.

### Likelihood Explanation
Likelihood is moderate: `RegisterPointer` is a permissionless, unprivileged transaction (any account can submit `MsgRegisterPointer`, per `x/evm/client/cli/native_tx.go` `RegisterCwPointerCmd`), so the precondition (register a pointer with a mismatched type) is trivially reachable by any user. Whether it results in fund loss depends on whether the pointer already exists (there is a duplicate-registration guard, `x/evm/keeper/msg_server.go:269-271`) and on downstream integrators trusting a pointer's declared type rather than independently verifying token behavior — this reduces but does not eliminate exploitability, since anyone (not just the token owner) can trigger registration and mislead third parties who look up pointers by declared `PointerType`.

### Recommendation
Before instantiating/migrating a pointer or persisting the EVM<->CW mapping in `RegisterPointer`, validate that the target EVM contract actually implements the declared standard (e.g., via ERC-165 `supportsInterface` for ERC721/ERC1155, and characteristic ERC20 methods such as `decimals()`/`totalSupply()` with expected return types for ERC20), and reject the registration if the check fails or is inconclusive, mirroring the "use safe, standard-specific verification instead of trusting a caller-supplied type tag" recommendation from the source report.

### Proof of Concept
1. Deploy a standard ERC20 token contract on the Sei EVM (e.g., `MyERC20`, with `transferFrom(address,address,uint256)` interpreting the third argument as a token amount).
2. Submit `MsgRegisterPointer{ PointerType: PointerType_ERC721, ErcAddress: <the ERC20 contract address> }` from any account (unprivileged; see `RegisterCwPointerCmd` in `x/evm/client/cli/native_tx.go:60-87`).
3. `ValidateBasic` passes (only checks hex address format) and `RegisterPointer` instantiates a CW721-pointer wasm contract with `erc721_address` set to the ERC20 address, then records the mapping via `SetCW721ERC721Pointer`, with no verification that the address is actually an ERC721 contract.
4. Any subsequent `transfer_nft`/`transferFrom`-style call routed through this pointer (or through `HandleERC721TransferPayload`, which packs `transferFrom(from, to, tokenId)` using the ERC721 ABI) executes against the underlying ERC20 contract and is interpreted as transferring `tokenId` units of the ERC20 token rather than a specific NFT, producing incorrect fund movement relative to caller/observer expectations.

### Citations

**File:** x/evm/types/message_register_pointer.go (L47-58)
```go
func (msg *MsgRegisterPointer) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}

	if !common.IsHexAddress(msg.ErcAddress) {
		return sdkerrors.ErrInvalidAddress
	}

	return nil
}
```

**File:** x/evm/keeper/msg_server.go (L247-298)
```go
func (server msgServer) RegisterPointer(goCtx context.Context, msg *types.MsgRegisterPointer) (*types.MsgRegisterPointerResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	if server.GetRegisterPointerDisabled(ctx) {
		return nil, fmt.Errorf("registering CW->ERC pointers has been disabled")
	}
	var existingPointer sdk.AccAddress
	var existingVersion uint16
	var currentVersion uint16
	var exists bool
	switch msg.PointerType {
	case types.PointerType_ERC20:
		currentVersion = erc20.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC721:
		currentVersion = erc721.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC1155:
		currentVersion = erc1155.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW1155ERC1155Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	default:
		panic("unknown pointer type")
	}
	if exists && existingVersion >= currentVersion {
		return nil, fmt.Errorf("pointer %s already registered at version %d", existingPointer.String(), existingVersion)
	}
	payload := map[string]interface{}{}
	switch msg.PointerType {
	case types.PointerType_ERC20:
		payload["erc20_address"] = msg.ErcAddress
	case types.PointerType_ERC721:
		payload["erc721_address"] = msg.ErcAddress
	case types.PointerType_ERC1155:
		payload["erc1155_address"] = msg.ErcAddress
	default:
		panic("unknown pointer type")
	}
	codeID := server.GetStoredPointerCodeID(ctx, msg.PointerType)
	moduleAcct := server.accountKeeper.GetModuleAddress(types.ModuleName)
	var err error
	var pointerAddr sdk.AccAddress
	if exists {
		bz, _ := json.Marshal(map[string]interface{}{})
		pointerAddr = existingPointer
		_, err = server.wasmKeeper.Migrate(ctx, existingPointer, moduleAcct, codeID, bz)
	} else {
		bz, jerr := json.Marshal(payload)
		if jerr != nil {
			return nil, jerr
		}
		pointerAddr, _, err = server.wasmKeeper.Instantiate(ctx, codeID, moduleAcct, moduleAcct, bz, fmt.Sprintf("Pointer of %s", msg.ErcAddress), sdk.NewCoins())
	}
	if err != nil {
```

**File:** x/evm/keeper/msg_server.go (L301-323)
```go
	switch msg.PointerType {
	case types.PointerType_ERC20:
		err = server.SetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc20"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc20.CurrentVersion))))
	case types.PointerType_ERC721:
		err = server.SetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc721"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc721.CurrentVersion))))
	case types.PointerType_ERC1155:
		err = server.SetCW1155ERC1155Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc1155"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc1155.CurrentVersion))))
	default:
		panic("unknown pointer type")
	}
	return &types.MsgRegisterPointerResponse{PointerAddress: pointerAddr.String()}, err
```
