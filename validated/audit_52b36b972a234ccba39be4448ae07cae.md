## Analysis

The reported bug class (missing `__gap` in upgradeable Solidity contracts causing storage-layout collisions on upgrade) has an analog in sei-chain's ERC pointer contracts, but the mechanism is different from OZ proxy patterns: instead of `delegatecall` against a separate implementation, sei-chain "upgrades" pointer contracts by **overwriting only the runtime code at a fixed contract address while leaving all existing storage untouched**.

### Finding Description

`Keeper.UpsertERCPointer` in `x/evm/keeper/pointer_upgrade.go` is the code path used to create/upgrade native, CW20, CW721 and CW1155 ERC pointer contracts. When a pointer already exists for a given `pointee`, it does not deploy a fresh contract; it calls `evm.GetDeploymentCode(evmModuleAddress, bin, suppliedGas, utils.Big0, existingAddr)` to re-run the new template's constructor at the *existing* address and then only overwrites the code via `k.SetCode(writeCtx, contractAddr, ret)`: [1](#0-0) 

Storage at `existingAddr` is never cleared or migrated — only `code` is replaced. This is confirmed by the accompanying test, which shows storage/code state (`sdb.SetCode`) surviving the "upgrade" call and only the code slot changing: [2](#0-1) 

This pattern is functionally identical to an OpenZeppelin upgradeable-proxy pattern (fixed address, swap-in-place logic, persistent storage) but it has none of the storage-layout protections `__gap` is meant to provide, and none of OZ's compile-time layout-compatibility checks. The pointer templates (`x/evm/artifacts/native`, `cw20`, `cw721`, `cw1155`) declare their own storage variables (`name`, `symbol`, `decimals`, balances/allowances mappings, etc.) directly in contract state without any explicit slot reservation. If a future template version changes declaration order or inserts a new state variable ahead of existing ones (exactly the change class the original report warns about), the new constructor will write into the "wrong" logical slots for any variables it doesn't explicitly re-initialize, while old values from the previous version remain in place at their old slot offsets — silently corrupting balances/allowances/metadata for the token represented by the pointer.

This same redeploy path is reachable by an unprivileged EVM caller through:
- the `pointer` precompile's `addNativePointer` / `addCW20Pointer` / `addCW721Pointer` / `addCW1155Pointer` methods, whose executors call the very same `evm.GetDeploymentCode`/`SetCode` pattern directly, e.g. in `AddNative`: [3](#0-2) 
- `MsgRegisterPointer` for CW→ERC pointers (a different, wasmd-`Migrate`-based upgrade path, but sharing the same "same address, persistent storage, code-only swap" hazard): [4](#0-3) 

### Impact Explanation

If a chain upgrade bumps a pointer template's storage layout (adds/reorders a state variable) without a corresponding migration, every already-registered pointer contract for that type (native/CW20/CW721/CW1155) would have its storage silently misaligned with the new code the first time anyone (any unprivileged user, since these calls are permissionless) triggers a re-upsert (e.g. by calling the pointer precompile again once `CurrentVersion` is bumped, as several `AddCW20`/`AddNative` handlers explicitly gate on `existingVersion >= CurrentVersion` and otherwise proceed to redeploy). This can corrupt token balances/allowances/metadata (fund loss/freezing for holders of the affected native/CW20/CW721/CW1155 asset routed through that pointer) chain-wide, since pointer contracts back real token transfers.

### Likelihood Explanation

This is not currently exploitable as a bug on its own — it requires a future storage-layout change to one of the pointer templates without a companion storage migration, which is exactly the class of accident `__gap`/layout discipline exists to prevent. Given there is no `__gap`, no fixed/append-only storage-layout convention, and no automated layout-compatibility check across the versioned templates (`v552`…`v620`+ directories show iterative changes to precompile executors already), the risk of an accidental layout-breaking change slipping through in a future release is realistic, and the blast radius (all pointer contracts of that ERC type) is large.

### Recommendation

- Adopt a fixed, append-only storage layout convention (or explicit storage struct with a reserved namespace/slot, similar to `ProxyERC20Storage` patterns already used in the test fixtures) for the native/CW20/CW721/CW1155 pointer templates in `x/evm/artifacts/*`, so new fields can only be appended, never inserted/reordered.
- Add a `__gap`-equivalent (reserved unused slots) to these templates to absorb small future additions safely, or use ERC-7201 namespaced storage.
- Add a compile-time/CI check (e.g. running `slither`/storage-layout diffing) across artifact versions to prevent an incompatible layout change from ever reaching `UpsertERCPointer`'s code-swap path.

### Proof of Concept

Not directly exploitable today (requires a future incompatible template change), so no working exploit transaction exists in the current codebase. The concrete mechanism proven from code/tests: call the pointer precompile (or trigger `RegisterPointer`) once for a token, then again after `CurrentVersion` increases — `UpsertERCPointer`/`AddNative` etc. redeploy new bytecode at the *same* address via `SetCode` while never touching storage, as shown by `TestUpsertERCNativePointerKeepsCodeCacheCoherent` and `TestUpsertERC20Pointer`, which both assert the address is retained across re-upserts with only code changing. [5](#0-4)

### Citations

**File:** x/evm/keeper/pointer_upgrade.go (L115-131)
```go
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
```

**File:** x/evm/keeper/pointer_upgrade_test.go (L76-126)
```go
func TestUpsertERCNativePointerKeepsCodeCacheCoherent(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now())
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

	stalePlant := []byte{1, 2, 3, 4, 5}
	var warmed, codeAfter, storeCode []byte
	var sizeAfter int
	err := k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
		addr, err := k.UpsertERCNativePointer(ctx, e, "cache-coherent", utils.ERCMetadata{
			Name:     "before",
			Symbol:   "before",
			Decimals: 6,
		})
		if err != nil {
			return err
		}
		sdb := state.GetDBImpl(e.StateDB)
		if sdb == nil {
			return errors.New("expected DBImpl StateDB")
		}
		// Distinct warm entry so a missed memo update is observable (metadata-only
		// redeploys often produce identical runtime bytecode).
		sdb.SetCode(addr, stalePlant)
		warmed = sdb.GetCode(addr)

		_, err = k.UpsertERCNativePointer(ctx, e, "cache-coherent", utils.ERCMetadata{
			Name:     "after",
			Symbol:   "after",
			Decimals: 8,
		})
		if err != nil {
			return err
		}
		codeAfter = sdb.GetCode(addr)
		sizeAfter = sdb.GetCodeSize(addr)
		storeCode = k.GetCode(sdb.Ctx(), addr)
		// Registry must be written on the live Multistore layer (sdb.Ctx), not the
		// Prepare-time ctx that GetDeploymentCode may have frozen.
		got, _, found := k.GetERC20NativePointer(sdb.Ctx(), "cache-coherent")
		if !found || got != addr {
			return fmt.Errorf("pointer registry not visible on live StateDB ctx: found=%v got=%s want=%s", found, got.Hex(), addr.Hex())
		}
		return nil
	}, func(string, string) {})
	require.NoError(t, err)
	require.Equal(t, stalePlant, warmed)
	require.NotEqual(t, warmed, codeAfter)
	require.Equal(t, len(codeAfter), sizeAfter)
	require.Equal(t, codeAfter, storeCode)
}
```

**File:** x/evm/keeper/pointer_upgrade_test.go (L177-202)
```go
func TestUpsertERC20Pointer(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now())
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
	var addr common.Address
	err := k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
		a, err := k.UpsertERCCW20Pointer(ctx, e, "test", utils.ERCMetadata{
			Name:   "test",
			Symbol: "test",
		})
		addr = a
		return err
	}, func(s1, s2 string) {})
	require.Nil(t, err)
	var newAddr common.Address
	err = k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
		a, err := k.UpsertERCCW20Pointer(ctx, e, "test", utils.ERCMetadata{
			Name:   "test2",
			Symbol: "test2",
		})
		newAddr = a
		return err
	}, func(s1, s2 string) {})
	require.Nil(t, err)
	require.Equal(t, addr, newAddr)
}
```

**File:** precompiles/pointer/legacy/v552/pointer.go (L138-194)
```go
func (p Precompile) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20NativePointer(ctx, token)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, native.CurrentVersion)
	}
	metadata, metadataExists := p.bankKeeper.GetDenomMetaData(ctx, token)
	if !metadataExists {
		return nil, 0, fmt.Errorf("denom %s does not have metadata stored and thus can only have its pointer set through gov proposal", token)
	}
	name := metadata.Name
	symbol := metadata.Symbol
	var decimals uint8
	for _, denomUnit := range metadata.DenomUnits {
		if denomUnit.Exponent > uint32(decimals) && denomUnit.Exponent <= math.MaxUint8 {
			decimals = uint8(denomUnit.Exponent)
			name = denomUnit.Denom
			symbol = denomUnit.Denom
			if len(denomUnit.Aliases) > 0 {
				name = denomUnit.Aliases[0]
			}
		}
	}
	constructorArguments := []interface{}{
		token, name, symbol, decimals,
	}

	packedArgs, err := native.GetParsedABI().Pack("", constructorArguments...)
	if err != nil {
		panic(err)
	}
	bin := append(native.GetBin(), packedArgs...)
	if value == nil {
		value = utils.Big0
	}
	ret, contractAddr, remainingGas, err := evm.Create(caller, bin, suppliedGas, uint256.MustFromBig(value))
	if err != nil {
		return
	}
	err = p.evmKeeper.SetERC20NativePointer(ctx, token, contractAddr)
	if err != nil {
		return
	}

	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "native"),
		sdk.NewAttribute(types.AttributeKeyPointerAddress, contractAddr.Hex()), sdk.NewAttribute(types.AttributeKeyPointee, token),
		sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", native.CurrentVersion))))
	ret, err = method.Outputs.Pack(contractAddr)
	return
}
```

**File:** x/evm/keeper/msg_server.go (L247-324)
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
}
```
