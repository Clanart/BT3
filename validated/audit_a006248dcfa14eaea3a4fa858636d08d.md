### Title
Pointer registration overwrites reverse pointer registry without cleanup, leaving stale forward/reverse mapping entries — ([File: x/evm/keeper/pointer.go])

### Summary
The EVM keeper's ERC/CW pointer registration functions (e.g. `SetERC20NativePointerWithVersion`, `SetERC20CW20PointerWithVersion`, `SetERC721CW721PointerWithVersion`, `SetERC1155CW1155PointerWithVersion` and their CW→ERC counterparts) each write two independent key-value entries — a forward pointer key and a `PointerReverseRegistryKey` entry — without checking whether the target address (`addr`) is already the reverse-registry key for a *different* pointee. This mirrors the "token → vault mapping can be overwritten" bug class from the referenced report: a forward mapping can be silently repointed while the corresponding reverse mapping for the old target is left stale, or a new forward write can create a reverse entry that collides with/overwrites an existing reverse entry belonging to a different pointee, producing inconsistent forward/reverse state.

### Finding Description
Each pointer setter follows the same pattern [1](#0-0) :
```go
func (k *Keeper) SetERC20NativePointerWithVersion(ctx sdk.Context, token string, addr common.Address, version uint16) error {
	if k.cwAddressIsPointer(ctx, token) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerERC20NativeKey(token), addr[:], version)
	...
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(addr), []byte(token), version)
}
```
The only guard applied is `cwAddressIsPointer`/`evmAddressIsPointer`, which checks whether the *pointee* (`token`/`cwAddress`/`erc*Address`) is itself already a pointer to something else [2](#0-1) . There is no check on whether the *target* `addr` being written into `PointerReverseRegistryKey(addr)` is already reverse-registered to a *different* pointee. The same asymmetry exists across all six analogous setters: `SetERC20CW20PointerWithVersion`, `SetERC721CW721PointerWithVersion`, `SetERC1155CW1155PointerWithVersion`, `SetCW20ERC20PointerWithVersion`, `SetCW721ERC721PointerWithVersion`, `SetCW1155ERC1155PointerWithVersion` [3](#0-2) .

This is reachable from public, unprivileged entry points:
- `RegisterPointer` message handler, which allows anyone to (re-)register a CW→ERC pointer for a given `ErcAddress`, migrating/reinstantiating the wasm pointer contract at a version-gated path [4](#0-3) .
- The `pointer` precompile's `AddCW20`/`AddNative`/`AddCW721` methods (via `UpsertERCPointer`) available to any EVM caller [5](#0-4) .

Because the setter functions write the reverse key unconditionally (only version-gated, not existence-gated against a *different* address), the design assumes 1:1 pointer/pointee relationships are enforced entirely by upstream "already registered" checks at the message-handler/precompile layer (e.g., `RegisterPointer`'s `if exists && existingVersion >= currentVersion` check, or the precompile's `existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(...)` check). If any code path calls the keeper setters directly with a stale/incorrect version, or if the version-gate logic diverges from the underlying key write logic (as already demonstrated by the module's own test suite exercising `DeleteXXXPointer` + `SetXXXPointerWithVersion` combos to simulate "upgrades" [6](#0-5) ), the reverse-registry entry for the old contract address is never cleaned up while a new forward entry can point elsewhere, or a new address can silently take over a reverse-registry slot that logically belongs to another pointee, exactly analogous to the `vaults[_token]`/`tokens[_vault]` mismatch in the referenced Yaxis finding.

### Impact Explanation
The reverse-registry mapping (`PointerReverseRegistryKey`) is the sole source of truth used by `cwAddressIsPointer` / `evmAddressIsPointer` to prevent "pointer to pointer" chains, and by `GetAnyPointerInfo` used to detect whether an address is a pointer at all. A stale or mismatched reverse entry can cause:
- A legitimate ERC/CW contract address to be permanently and incorrectly flagged as a "pointer", blocking future pointer creation to/from it (`ErrorPointerToPointerNotAllowed`), effectively bricking that address's ability to participate in the pointer system.
- Divergence between forward and reverse mappings, so that `GetAnyPointerInfo`/`GetAnyPointeeInfo` return incorrect information used by downstream consumers (bridging logic, balance/ownership assumptions for wrapped tokens moving between CW and EVM representations) — this is the CW↔EVM bridge invariant that pointer correctness underpins.

### Likelihood Explanation
The forward-checks currently in place (`RegisterPointer`'s version comparison, and the `pointer` precompile's `existingVersion` comparisons) appear to prevent naive re-registration under normal operation, so triggering this specific stale-reverse-mapping condition requires a version/state combination not fully covered by those upstream guards (as the module's own tests intentionally construct via manual `Delete...` + `Set...WithVersion` calls to simulate upgrades). I could not find a fully unprivileged, single-transaction path in the current code that reaches a genuinely inconsistent state without such a version manipulation, so likelihood is Medium and is bounded by upstream gating logic that partially, but not structurally, protects the underlying keeper functions from this mapping-overwrite class of bug.

### Recommendation
Add symmetric validation to the pointer setter functions in `x/evm/keeper/pointer.go`: before writing `PointerReverseRegistryKey(addr)`, verify that no existing reverse entry for `addr` maps to a *different* pointee; if a stale association is being replaced (e.g., during an intentional upgrade), explicitly delete the reverse entry for the previous forward-mapped address before writing the new one, mirroring the `DeleteXXXPointer` cleanup logic that already exists in this file. This closes the "one address, one pointer" invariant at the data-layer instead of relying entirely on message-handler-level version checks.

### Proof of Concept
Not independently verified end-to-end due to ask-only/index constraints — the concrete trigger requires reconstructing the exact `RegisterPointer` version-gating interaction (as the codebase's own `TestRegisterPointer` test does via manual `DeleteCW20ERC20Pointer` + `SetCW20ERC20PointerWithVersion` calls [6](#0-5) ) to determine whether an unprivileged sequence of `MsgRegisterPointer` calls alone (without direct keeper access) can reach the inconsistent state. This would need to be confirmed by tracing all callers of the six `Set*PointerWithVersion` functions and their version arguments in a live/test environment, which is beyond what static code search can conclusively establish here.

### Citations

**File:** x/evm/keeper/pointer.go (L33-43)
```go
// ERC20 -> Native Token
func (k *Keeper) SetERC20NativePointerWithVersion(ctx sdk.Context, token string, addr common.Address, version uint16) error {
	if k.cwAddressIsPointer(ctx, token) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerERC20NativeKey(token), addr[:], version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(addr), []byte(token), version)
}
```

**File:** x/evm/keeper/pointer.go (L63-282)
```go
// ERC20 -> CW20
func (k *Keeper) SetERC20CW20Pointer(ctx sdk.Context, cw20Address string, addr common.Address) error {
	return k.SetERC20CW20PointerWithVersion(ctx, cw20Address, addr, cw20.CurrentVersion(ctx))
}

// ERC20 -> CW20
func (k *Keeper) SetERC20CW20PointerWithVersion(ctx sdk.Context, cw20Address string, addr common.Address, version uint16) error {
	if k.cwAddressIsPointer(ctx, cw20Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerERC20CW20Key(cw20Address), addr[:], version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(addr), []byte(cw20Address), version)
}

// ERC20 -> CW20
func (k *Keeper) GetERC20CW20Pointer(ctx sdk.Context, cw20Address string) (addr common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetPointerInfo(ctx, types.PointerERC20CW20Key(cw20Address), cw20.CurrentVersion(ctx))
	if exists {
		addr = common.BytesToAddress(addrBz)
	}
	return
}

// ERC20 -> CW20
func (k *Keeper) DeleteERC20CW20Pointer(ctx sdk.Context, cw20Address string, version uint16) {
	addr, _, exists := k.GetERC20CW20Pointer(ctx, cw20Address)
	if exists {
		k.deletePointerInfo(ctx, types.PointerERC20CW20Key(cw20Address), version)
		k.deletePointerInfo(ctx, types.PointerReverseRegistryKey(addr), version)
	}
}

// ERC721 -> CW721
func (k *Keeper) SetERC721CW721Pointer(ctx sdk.Context, cw721Address string, addr common.Address) error {
	return k.SetERC721CW721PointerWithVersion(ctx, cw721Address, addr, cw721.CurrentVersion)
}

// ERC721 -> CW721
func (k *Keeper) SetERC721CW721PointerWithVersion(ctx sdk.Context, cw721Address string, addr common.Address, version uint16) error {
	if k.cwAddressIsPointer(ctx, cw721Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerERC721CW721Key(cw721Address), addr[:], version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(addr), []byte(cw721Address), version)
}

// ERC721 -> CW721
func (k *Keeper) GetERC721CW721Pointer(ctx sdk.Context, cw721Address string) (addr common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetPointerInfo(ctx, types.PointerERC721CW721Key(cw721Address), cw721.CurrentVersion)
	if exists {
		addr = common.BytesToAddress(addrBz)
	}
	return
}

// ERC721 -> CW721
func (k *Keeper) DeleteERC721CW721Pointer(ctx sdk.Context, cw721Address string, version uint16) {
	addr, _, exists := k.GetERC721CW721Pointer(ctx, cw721Address)
	if exists {
		k.deletePointerInfo(ctx, types.PointerERC721CW721Key(cw721Address), version)
		k.deletePointerInfo(ctx, types.PointerReverseRegistryKey(addr), version)
	}
}

// ERC1155 -> CW1155
func (k *Keeper) SetERC1155CW1155Pointer(ctx sdk.Context, cw1155Address string, addr common.Address) error {
	return k.SetERC1155CW1155PointerWithVersion(ctx, cw1155Address, addr, cw1155.CurrentVersion)
}

// ERC1155 -> CW1155
func (k *Keeper) SetERC1155CW1155PointerWithVersion(ctx sdk.Context, cw1155Address string, addr common.Address, version uint16) error {
	if k.cwAddressIsPointer(ctx, cw1155Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerERC1155CW1155Key(cw1155Address), addr[:], version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(addr), []byte(cw1155Address), version)
}

// ERC1155 -> CW1155
func (k *Keeper) GetERC1155CW1155Pointer(ctx sdk.Context, cw1155Address string) (addr common.Address, version uint16, exists bool) {
	addrBz, version, exists := k.GetPointerInfo(ctx, types.PointerERC1155CW1155Key(cw1155Address), cw1155.CurrentVersion)
	if exists {
		addr = common.BytesToAddress(addrBz)
	}
	return
}

// ERC1155 -> CW1155
func (k *Keeper) DeleteERC1155CW1155Pointer(ctx sdk.Context, cw1155Address string, version uint16) {
	addr, _, exists := k.GetERC1155CW1155Pointer(ctx, cw1155Address)
	if exists {
		k.deletePointerInfo(ctx, types.PointerERC1155CW1155Key(cw1155Address), version)
		k.deletePointerInfo(ctx, types.PointerReverseRegistryKey(addr), version)
	}
}

// CW20 -> ERC20
func (k *Keeper) SetCW20ERC20Pointer(ctx sdk.Context, erc20Address common.Address, addr string) error {
	return k.SetCW20ERC20PointerWithVersion(ctx, erc20Address, addr, erc20.CurrentVersion)
}

// CW20 -> ERC20
func (k *Keeper) SetCW20ERC20PointerWithVersion(ctx sdk.Context, erc20Address common.Address, addr string, version uint16) error {
	if k.evmAddressIsPointer(ctx, erc20Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerCW20ERC20Key(erc20Address), []byte(addr), version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc20Address[:], version)
}

// CW20 -> ERC20
func (k *Keeper) GetCW20ERC20Pointer(ctx sdk.Context, erc20Address common.Address) (addr sdk.AccAddress, version uint16, exists bool) {
	addrBz, version, exists := k.GetPointerInfo(ctx, types.PointerCW20ERC20Key(erc20Address), erc20.CurrentVersion)
	if exists {
		addr = sdk.MustAccAddressFromBech32(string(addrBz))
	}
	return
}

// CW20 -> ERC20
func (k *Keeper) DeleteCW20ERC20Pointer(ctx sdk.Context, erc20Address common.Address, version uint16) {
	addr, _, exists := k.GetCW20ERC20Pointer(ctx, erc20Address)
	if exists {
		k.deletePointerInfo(ctx, types.PointerCW20ERC20Key(erc20Address), version)
		k.deletePointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr.String()))), version)
	}
}

func (k *Keeper) evmAddressIsPointer(ctx sdk.Context, addr common.Address) bool {
	_, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(addr))
	return exists
}

func (k *Keeper) cwAddressIsPointer(ctx sdk.Context, addr string) bool {
	_, _, exists := k.GetAnyPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))))
	return exists
}

// CW721 -> ERC721
func (k *Keeper) SetCW721ERC721Pointer(ctx sdk.Context, erc721Address common.Address, addr string) error {
	return k.SetCW721ERC721PointerWithVersion(ctx, erc721Address, addr, erc721.CurrentVersion)
}

// CW721 -> ERC721
func (k *Keeper) SetCW721ERC721PointerWithVersion(ctx sdk.Context, erc721Address common.Address, addr string, version uint16) error {
	if k.evmAddressIsPointer(ctx, erc721Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerCW721ERC721Key(erc721Address), []byte(addr), version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc721Address[:], version)
}

// CW721 -> ERC721
func (k *Keeper) GetCW721ERC721Pointer(ctx sdk.Context, erc721Address common.Address) (addr sdk.AccAddress, version uint16, exists bool) {
	addrBz, version, exists := k.GetPointerInfo(ctx, types.PointerCW721ERC721Key(erc721Address), erc721.CurrentVersion)
	if exists {
		addr = sdk.MustAccAddressFromBech32(string(addrBz))
	}
	return
}

// CW721 -> ERC721
func (k *Keeper) DeleteCW721ERC721Pointer(ctx sdk.Context, erc721Address common.Address, version uint16) {
	addr, _, exists := k.GetCW721ERC721Pointer(ctx, erc721Address)
	if exists {
		k.deletePointerInfo(ctx, types.PointerCW721ERC721Key(erc721Address), version)
		k.deletePointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr.String()))), version)
	}
}

// CW1155 -> ERC1155
func (k *Keeper) SetCW1155ERC1155Pointer(ctx sdk.Context, erc1155Address common.Address, addr string) error {
	return k.SetCW1155ERC1155PointerWithVersion(ctx, erc1155Address, addr, erc1155.CurrentVersion)
}

// CW1155 -> ERC1155
func (k *Keeper) SetCW1155ERC1155PointerWithVersion(ctx sdk.Context, erc1155Address common.Address, addr string, version uint16) error {
	if k.evmAddressIsPointer(ctx, erc1155Address) {
		return ErrorPointerToPointerNotAllowed
	}
	err := k.setPointerInfo(ctx, types.PointerCW1155ERC1155Key(erc1155Address), []byte(addr), version)
	if err != nil {
		return err
	}
	return k.setPointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr))), erc1155Address[:], version)
}

// CW1155 -> ERC1155
func (k *Keeper) GetCW1155ERC1155Pointer(ctx sdk.Context, erc1155Address common.Address) (addr sdk.AccAddress, version uint16, exists bool) {
	addrBz, version, exists := k.GetPointerInfo(ctx, types.PointerCW1155ERC1155Key(erc1155Address), erc1155.CurrentVersion)
	if exists {
		addr = sdk.MustAccAddressFromBech32(string(addrBz))
	}
	return
}

// CW1155 -> ERC1155
func (k *Keeper) DeleteCW1155ERC1155Pointer(ctx sdk.Context, erc1155Address common.Address, version uint16) {
	addr, _, exists := k.GetCW1155ERC1155Pointer(ctx, erc1155Address)
	if exists {
		k.deletePointerInfo(ctx, types.PointerCW1155ERC1155Key(erc1155Address), version)
		k.deletePointerInfo(ctx, types.PointerReverseRegistryKey(common.BytesToAddress([]byte(addr.String()))), version)
	}
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

**File:** x/evm/keeper/pointer_upgrade.go (L89-146)
```go
func (k *Keeper) UpsertERCPointer(
	ctx sdk.Context, evm *vm.EVM, typ string, args []interface{}, getter PointerGetter, setter PointerSetter,
) (contractAddr common.Address, err error) {
	pointee := args[0].(string)
	evmModuleAddress := k.GetEVMAddressOrDefault(ctx, k.AccountKeeper().GetModuleAddress(types.ModuleName))

	var bin []byte
	bin, err = artifacts.GetParsedABI(typ).Pack("", args...)
	if err != nil {
		panic(err)
	}
	bin = append(artifacts.GetBin(typ), bin...)
	// GetDeploymentCode / Create take EVM snapshots that Freeze() Multistore layers.
	// Exists-lookup and commits must use the live unfrozen top (sdb.Ctx): cachekv
	// forbids writing a frozen layer, and same-tx readers that skip frozen-empty
	// parents would miss those writes. The precompile Prepare `ctx` is that top at
	// Prepare time, but is frozen once this Upsert snapshots. Always attach the
	// caller's gas meter (finite precompile meter in deliver) — sdb.Ctx() alone
	// carries the infinite EVM meter.
	sdb := state.GetDBImpl(evm.StateDB)
	liveCtx := func() sdk.Context {
		if sdb == nil {
			return ctx
		}
		return sdb.Ctx().WithGasMeter(ctx.GasMeter())
	}
	existingAddr, _, exists := getter(liveCtx(), pointee)
	suppliedGas := k.getEvmGasLimitFromCtx(ctx)
	var remainingGas uint64
	if exists {
		var ret []byte
		contractAddr = existingAddr
		ret, remainingGas, err = evm.GetDeploymentCode(evmModuleAddress, bin, suppliedGas, utils.Big0, existingAddr)
		if err != nil {
			return
		}
		// Only write on success: a failed GetDeploymentCode can leave ret as nil or
		// revert data, which must not clobber live pointer bytecode (even transiently).
		writeCtx := liveCtx()
		k.SetCode(writeCtx, contractAddr, ret)
		if sdb != nil {
			sdb.RefreshCodeCache(contractAddr, ret)
		}
	} else {
		_, contractAddr, remainingGas, err = evm.Create(evmModuleAddress, bin, suppliedGas, uint256.NewInt(0))
	}
	if err != nil {
		return
	}
	ctx.GasMeter().ConsumeGas(k.GetCosmosGasLimitFromEVMGas(ctx, suppliedGas-remainingGas), "ERC pointer deployment")
	if err = setter(liveCtx(), pointee, contractAddr); err != nil {
		return
	}
	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, typ),
		sdk.NewAttribute(types.AttributeKeyPointerAddress, contractAddr.Hex()), sdk.NewAttribute(types.AttributeKeyPointee, pointee)))
	return
}
```

**File:** x/evm/keeper/msg_server_test.go (L580-593)
```go
	// upgrade ERC20 pointer
	k.DeleteCW20ERC20Pointer(ctx, pointee, version)
	k.SetCW20ERC20PointerWithVersion(ctx, pointee, pointer.String(), version-1)
	res, err = keeper.NewMsgServerImpl(k).RegisterPointer(sdk.WrapSDKContext(ctx), &types.MsgRegisterPointer{
		Sender:      sender.String(),
		PointerType: types.PointerType_ERC20,
		ErcAddress:  pointee.Hex(),
	})
	require.Nil(t, err)
	newPointer, version, exists := k.GetCW20ERC20Pointer(ctx, pointee)
	require.True(t, exists)
	require.Equal(t, erc20.CurrentVersion, version)
	require.Equal(t, newPointer.String(), res.PointerAddress)
	require.Equal(t, newPointer.String(), pointer.String()) // should retain the existing contract address
```
