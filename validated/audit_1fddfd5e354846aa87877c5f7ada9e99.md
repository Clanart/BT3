### Title
Type-confused CW->ERC pointer creation via `RegisterPointer` — no verification that `ErcAddress` implements the claimed `PointerType` interface - (File: `x/evm/keeper/msg_server.go`)

### Summary
The original report's bug class is: a factory/feed constructor accepts a caller-supplied address and *assumes* it corresponds to a given, well-formed contract type (a Uniswap pool with specific tokens) without independently verifying that assumption, and the deployment entrypoint (`deployFeed()`) is permissionless. The sei-chain analog is `MsgServer.RegisterPointer` in `x/evm/keeper/msg_server.go`, which accepts a caller-supplied `ErcAddress` and a `PointerType` (`ERC20`/`ERC721`/`ERC1155`) and instantiates a CosmWasm pointer contract that will forever treat that address as being of the declared type — without ever checking that the address contains code, let alone that it actually implements the claimed ERC interface.

### Finding Description
`MsgRegisterPointer.ValidateBasic()` only checks that `ErcAddress` is syntactically a hex address: [1](#0-0) 

`msgServer.RegisterPointer` then unconditionally uses this attacker-supplied `(ErcAddress, PointerType)` pair to instantiate (or migrate) a CosmWasm pointer contract, passing `erc20_address`/`erc721_address`/`erc1155_address` into the payload with no check that code exists at `ErcAddress` or that it implements the declared interface: [2](#0-1) [3](#0-2) 

This is only gated by a single global on/off toggle (`RegisterPointerDisabled`), not by any per-request legitimacy check of the pointee: [4](#0-3) 

The resulting CW pointer contract (e.g. the Solidity-side equivalent shown by `CW20ERC20Pointer.sol`, illustrating the same "trust the stored pointee address blindly" pattern used on the CW->ERC direction) executes state-changing calls (`transfer`, `transferFrom`, etc.) against the stored pointee address and treats a successful low-level call as proof that the operation happened: [5](#0-4) 

Because EVM semantics return `success = true` with empty return data when calling an address that has no code, and because nothing in `RegisterPointer` verifies `GetCode(ErcAddress)` is non-empty or matches the declared token standard, any unprivileged Sei account can call `RegisterPointer` to spin up a CW20/CW721/CW1155 pointer for:
- a plain externally-owned account (no code at all), or
- a contract of a different type than declared (e.g. registering an ERC20 contract as `PointerType_ERC721`).

This exactly mirrors the reported bug class: a "partial/no check" on whether the supplied address is really what it's claimed to be, reachable through a permissionless entry point (`RegisterPointer`, analogous to `deployFeed()`).

### Impact Explanation
A pointer created for a non-existent or mismatched-type EVM address becomes a CosmWasm-facing token whose `transfer`/`execute` calls into the EVM side will not revert (because the underlying `.call`/precompile invocation to a codeless or wrong-ABI address still returns success in EVM semantics), while never moving any real value. Any protocol, exchange, or user on the Cosmos/CosmWasm side that observes the CW pointer's execute-success or synthetic events as proof of a token transfer/payment can be deceived into crediting funds or goods for a transfer that never happened. This is a fund-loss/spoofing vector reachable by any transaction sender (no privilege required beyond a live governance toggle that defaults open), and once created, the mis-typed pointer persists in state permanently (barring another governance-controlled migrate), so the confusion/fraud opportunity is durable, matching the "verify pool legitimacy"-class root cause of unverified type assumptions on caller-supplied addresses.

### Likelihood Explanation
`RegisterPointer` is callable by any account paying gas, gated only by a single boolean flag (`RegisterPointerDisabled`) that does not perform any per-address verification. `ValidateBasic` performs no code-existence or interface check. No other check anywhere in the message handling path (`x/evm/keeper/msg_server.go:247-324`) validates that `ErcAddress` actually implements the declared `PointerType`. This makes exploitation trivial and always available whenever pointer registration is enabled (the default operational state, per the tests in `x/evm/keeper/msg_server_test.go`).

### Recommendation
- Before instantiating/migrating the pointer contract in `RegisterPointer`, verify `evmKeeper.GetCode(ctx, common.HexToAddress(msg.ErcAddress))` is non-empty (reject EOAs).
- Perform an EVM `staticcall`/interface probe (e.g. `supportsInterface` where available, or attempt the minimal ERC20/721/1155 read calls such as `totalSupply()`/`ownerOf()`/`balanceOf(address,uint256)`) to confirm the target actually conforms to the declared `PointerType` before creating the pointer, analogous to using `getPool()` to confirm pool legitimacy in the original report.
- Consider requiring the caller to supply enough evidence (e.g. a successful `ERC165`/standard-specific probe call) so the chain — not the caller — determines the type, rather than trusting the caller's `PointerType` claim outright.

### Proof of Concept
1. Attacker generates a fresh Sei-associated EVM address `E` with **no contract code deployed** (a plain EOA, or an account that has an EVM address mapping but never deployed a contract).
2. Attacker submits `MsgRegisterPointer{ Sender: attacker, PointerType: PointerType_ERC20, ErcAddress: E.Hex() }`.
3. `ValidateBasic` passes (valid hex address) and `RegisterPointer` (`x/evm/keeper/msg_server.go:247-297`) instantiates a CW20 pointer wasm contract bound to `E` with no check that `E` has code.
4. Any third party interacting with this pointer (e.g. calling `execute { transfer { recipient, amount } }` through the pointer) will have the pointer's underlying EVM call to `E` "succeed" with empty return data (standard EVM no-code-call semantics), producing an apparently successful transfer/synthetic event without any real value moving, which can be used to defraud counterparties who treat the pointer's success as proof of payment.

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

**File:** x/evm/keeper/msg_server.go (L247-297)
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
```

**File:** x/evm/keeper/msg_server.go (L301-324)
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
}
```

**File:** contracts/src/CW20ERC20Pointer.sol (L79-109)
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
