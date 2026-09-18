Confirmed: `MsgRegisterPointer.ValidateBasic()` only checks that `ErcAddress` is a syntactically valid hex address — it never checks that the address actually has contract code, let alone that it implements the interface matching the sender-supplied `PointerType`. [1](#0-0) 

### Title
`RegisterPointer` lets any caller bind an arbitrary/type-mismatched or non-contract EVM address as an ERC20/ERC721/ERC1155 pointer target - ([File: x/evm/keeper/msg_server.go])

### Summary
The Blueberry finding flags that `addBank()` restricts the underlying `token` to a whitelist but never validates the paired `cToken`, so an admin (or in a permissionless variant, any caller) can pair a token with an arbitrary/incompatible cToken. The analogous pattern exists in sei-chain's CW↔EVM pointer registration: `MsgServer.RegisterPointer` takes a fully user-controlled `ErcAddress` and a user-controlled `PointerType` enum (ERC20/ERC721/ERC1155) and instantiates a CW pointer contract for it without validating that the address is even a contract, let alone that it implements the claimed interface.

### Finding Description
`RegisterPointer` in `x/evm/keeper/msg_server.go` dispatches purely on the caller-supplied `msg.PointerType`, builds a payload keyed as `erc20_address`/`erc721_address`/`erc1155_address`, and instantiates (or migrates) a CosmWasm pointer contract bound to `msg.ErcAddress` — with zero verification that the target address is a deployed contract or that its bytecode actually implements the declared token standard. [2](#0-1) 

`ValidateBasic` for `MsgRegisterPointer` only checks the sender bech32 and that `ErcAddress` is well-formed hex — no code-existence or interface check. [1](#0-0) 

This is confirmed reachable even with the zero address (no code at all): `x/evm/migrations/migrate_all_pointers_test.go` registers an ERC721 pointer for `0x0000000000000000000000000000000000000000` and it succeeds. [3](#0-2) 

By contrast, the EVM-side pointer precompile (`AddNative`, `AddCW20`) does perform real validation before creating a pointer — `AddNative` requires bank denom metadata to exist, and `AddCW20` performs a `QuerySmart` `token_info` call against the target CW address, which fails if the target isn't a real CW20-compatible contract. [4](#0-3)  The CW→EVM path (`RegisterPointer`) has no equivalent check — it is the "cToken" side of the pairing that goes unchecked, exactly mirroring the reported bug class.

### Impact Explanation
An attacker (any unprivileged Sei address, no privilege required since `RegisterPointer` has no admin gating beyond a global `RegisterPointerDisabled` param) can:
- Register a pointer of a false type (e.g., declare an ERC20 contract as `PointerType_ERC721` or vice versa), producing a CW pointer contract that assumes a semantic interface the underlying EVM contract does not actually provide.
- Register a pointer for a non-contract / EOA / zero-code address, producing a pointer contract that will make EVM calls to code that doesn't exist. Under EVM semantics, calls to an address with no code always "succeed" trivially with empty returndata; depending on how the generated CW pointer's query/execute handlers decode that empty returndata, this could manifest as spurious zero balances/no-op transfers reported as successful, misleading integrators, indexers, or downstream contracts that trust `GetCW*ERC*Pointer` mappings as canonical registries for a given ERC address.
- Because `RegisterPointer` records the pointer address in the canonical `CW20ERC20Pointer`/`CW721ERC721Pointer`/`CW1155ERC1155Pointer` registries keyed by the raw `ErcAddress`, the first pointer/type registered for a given address becomes semi-permanent (later attempts to re-register just get "already registered" until a version bump), so a malicious/mistaken mismatched registration can squat on that address's canonical pointer slot.

This satisfies the "unauthorized transfer via precompile or pointer" / registry-integrity impact category, though the concrete depth of exploitable fund loss depends on how the generated CW pointer contract (deployed CosmWasm bytecode, not fully inspectable via the index) decodes empty/mismatched ABI return data on execute paths such as `transfer`/`transfer_from` — this could not be fully verified against the actual on-chain WASM bytecode within the available tooling.

### Likelihood Explanation
Likelihood is high for the type-confusion / no-code registration itself: it requires only a single `MsgRegisterPointer` transaction from any account, no special permissions, and is directly demonstrated as reachable by the existing migration test that registers a pointer for the zero address. [3](#0-2)  Whether this leads to concrete fund loss depends on downstream CW pointer contract behavior that could not be fully confirmed from the indexed sources.

### Recommendation
In `RegisterPointer` (`x/evm/keeper/msg_server.go`), before instantiating/migrating a CW pointer:
- Verify `msg.ErcAddress` has non-empty EVM bytecode (reject zero-code/EOA addresses).
- Verify the bytecode actually supports the interface implied by `msg.PointerType` — e.g. via an ERC165 `supportsInterface` check for ERC721/ERC1155, and a best-effort static call to `decimals()`/`totalSupply()` for ERC20 (mirroring what `AddCW20`/`AddNative` already do on the EVM-precompile side by querying `token_info` / requiring stored denom metadata) — and reject/roll back the pointer creation if the target does not conform.

### Proof of Concept
1. As any account, submit `MsgRegisterPointer{PointerType: PointerType_ERC721, ErcAddress: "0x00...00"}` (or any EOA/non-contract address). `ValidateBasic` accepts it because it only checks hex format. [1](#0-0) 
2. `RegisterPointer` proceeds to instantiate a CW721 pointer contract keyed to that address with no code/interface check, as shown by the existing test that does exactly this and succeeds. [3](#0-2) 
3. Alternatively, register `PointerType_ERC20` for an address that is actually a deployed ERC721 (or vice versa); `RegisterPointer` performs no interface check and will happily create the mismatched pointer, since the only gating logic is the "already registered at this or higher version" check. [5](#0-4)

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

**File:** x/evm/keeper/msg_server.go (L247-323)
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
		return nil, err
	}
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

**File:** x/evm/migrations/migrate_all_pointers_test.go (L97-114)
```go
func TestMigrateCWERC721Pointers(t *testing.T) {
	k := testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now())
	params := k.GetParams(ctx)
	params.RegisterPointerDisabled = false
	k.SetParams(ctx, params)
	require.Nil(t, migrations.StoreCWPointerCode(ctx, &k, false, true, false))
	msgServer := keeper.NewMsgServerImpl(&k)
	res, err := msgServer.RegisterPointer(sdk.WrapSDKContext(ctx), &types.MsgRegisterPointer{
		PointerType: types.PointerType_ERC721,
		ErcAddress:  "0x0000000000000000000000000000000000000000",
	})
	require.Nil(t, err)
	require.Nil(t, migrations.MigrateCWERC721Pointers(ctx, &k))
	// address should stay the same
	addr, _, _ := k.GetCW721ERC721Pointer(ctx, common.HexToAddress("0x0000000000000000000000000000000000000000"))
	require.Equal(t, res.PointerAddress, addr.String())
}
```

**File:** precompiles/pointer/legacy/v552/pointer.go (L196-220)
```go
func (p Precompile) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)
	if exists {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw20.CurrentVersion(ctx))
	}
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmart(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
```
