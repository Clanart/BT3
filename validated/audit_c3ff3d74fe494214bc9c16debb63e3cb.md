This confirms the analog: the test `TestUpsertERCNativePointer` explicitly demonstrates that calling `UpsertERCNativePointer` twice on the same token with different metadata (decimals 6 → 12) redeploys the pointer contract in place at the same address, changing `decimals()` from 6 to 12. [1](#0-0) 

### Title
Token factory denom admin can mutate ERC20 native pointer decimals/name/symbol post-deployment via `AddNativePointer`, corrupting integrations - (File: x/evm/keeper/pointer_upgrade.go)

### Summary
The `NativeSeiTokensERC20` pointer contract stores `decimals`, `name`, and `symbol` as ordinary constructor-set state variables (not immutable/frozen), derived from the token factory denom's bank `Metadata`. [2](#0-1)  A token factory denom admin can call `MsgSetDenomMetadata` at any time to change the denom's `Exponent` (decimals), with no freeze mechanism analogous to `publicMintPriceFrozen`/`presaleMintPriceFrozen` in the referenced report. [3](#0-2)  Once metadata is changed, anyone can call the public `pointer` precompile's `addNativePointer` method again for the same denom, which re-derives `decimals` from the updated metadata and overwrites the deployed pointer's bytecode in place at the same address. [4](#0-3) [5](#0-4) 

### Finding Description
`AddNative` in the pointer precompile reads the current bank denom metadata and computes `decimals` from `DenomUnits[].Exponent`, then calls `UpsertERCNativePointer`. [6](#0-5)  `UpsertERCPointer` checks if a pointer already exists for the token; if it does, it computes new deployment bytecode with the new constructor args (name/symbol/decimals) and calls `k.SetCode(writeCtx, contractAddr, ret)`, overwriting the existing contract's bytecode at the same address rather than creating a new one. [7](#0-6)  This is unit-tested and confirmed: `TestUpsertERCNativePointer` upserts the same token twice with `Decimals: 6` then `Decimals: 12` and asserts the same contract address now reports `decimals() == 12`. [8](#0-7) 

Crucially, the pointer's decimals/name/symbol are not protected by any admin-authorization check in the precompile path — `AddNative` only requires that bank denom metadata exists for the token, with no check that the caller is the tokenfactory denom admin. [9](#0-8)  Meanwhile, the tokenfactory denom admin (an ordinary, unprivileged user who created the denom) can freely call `SetDenomMetadata` on their own denom at any time with no restriction preventing later changes to decimals, since only an admin-match check is performed and no immutability/freeze flag exists. [3](#0-2)  Combining these two unprivileged, permissionless calls (`MsgSetDenomMetadata` then `addNativePointer`), a denom admin can silently change the `decimals()` value returned by an already-deployed, already-integrated ERC20 pointer contract that other unprivileged users/contracts (DEXs, lending protocols, wallets) rely on as an immutable ERC20 property.

This directly parallels the referenced NFTCollection bug class: a "mutable parameter that downstream integrators assume is fixed/frozen" (mint price there, ERC20 decimals here) can be changed unilaterally by the entity that controls it, with no on-chain freeze mechanism, enabling a rug against users/protocols that already built positions or pricing logic on the original value.

### Impact Explanation
Changing `decimals()` in place for an already-deployed ERC20 pointer can cause severe value miscalculation in any downstream integration that reads `decimals()` once and caches it, or computes value based on it (AMMs, lending/collateral valuation, price oracles, wallets displaying balances). A change from 6 to 12 decimals, e.g., inflates or deflates computed value by orders of magnitude (10^6 in this example), enabling under-collateralized borrowing, mispriced swaps, or direct fund loss for users who transact against stale decimal assumptions immediately after the covert change. This satisfies the "unauthorized transfer via precompile or pointer" / "fund loss" bar because the pointer/ERC20 semantics that other unprivileged transaction senders and pointer users rely on are silently and unilaterally rewritten.

### Likelihood Explanation
Both preconditions are reachable by a single unprivileged, permissionless transaction sender: (1) the tokenfactory denom admin is simply whoever created the denom via `MsgCreateDenom`, who becomes admin by default, [10](#0-9)  and (2) `addNativePointer` on the pointer precompile is callable by any EVM caller with no admin check. [9](#0-8)  No governance, validator, or operator privilege is required — an attacker can create a token, get others to integrate with its pointer (e.g., list it on a DEX or lending market), then flip the metadata and re-trigger the pointer upsert to corrupt the decimals used by all subsequent interactions.

### Recommendation
Freeze ERC20 pointer metadata (name/symbol/decimals) once minted/deployed, or disallow `UpsertERCPointer` from changing `decimals` for an existing pointer (only allow re-upsert for code/logic upgrades that keep observable interface constants — name/symbol/decimals — unchanged). Alternatively, require that `SetDenomMetadata` cannot alter `Exponent` (decimals) for a denom once a pointer has been registered for it, or require the change to go through the same governance-only path used when metadata doesn't exist (`"denom %s does not have metadata stored and thus can only have its pointer set through gov proposal"`). [11](#0-10) 

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/attacker/token`, becoming its admin. [12](#0-11) 
2. Attacker sets initial metadata with `Exponent: 6` via `MsgSetDenomMetadata`, then calls `addNativePointer` on the pointer precompile to deploy the ERC20 pointer with `decimals() == 6`. [13](#0-12) 
3. Third parties integrate with the pointer (list on DEX/lending market), relying on `decimals() == 6` being fixed.
4. Attacker calls `MsgSetDenomMetadata` again, changing `Exponent` to `12` (no freeze prevents this). [3](#0-2) 
5. Attacker (or anyone) calls `addNativePointer` again for the same denom; `UpsertERCPointer` detects the existing pointer and overwrites its bytecode in place, now returning `decimals() == 12` at the same contract address, as demonstrated by `TestUpsertERCNativePointer`. [14](#0-13) 
6. Any protocol that cached or assumed `decimals() == 6` now mis-prices the token by 10^6, allowing the attacker to extract value (e.g., borrow far more than collateral is worth, or execute favorable swaps against a mispriced pool).

### Citations

**File:** x/evm/keeper/pointer_upgrade_test.go (L33-71)
```go
func TestUpsertERCNativePointer(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now())
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
	var addr common.Address
	err := k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
		a, err := k.UpsertERCNativePointer(ctx, e, "test", utils.ERCMetadata{
			Name:     "test",
			Symbol:   "test",
			Decimals: 6,
		})
		addr = a
		return err
	}, func(s1, s2 string) {})
	require.Nil(t, err)
	var newAddr common.Address
	err = k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
		a, err := k.UpsertERCNativePointer(ctx, e, "test", utils.ERCMetadata{
			Name:     "test2",
			Symbol:   "test2",
			Decimals: 12,
		})
		newAddr = a
		return err
	}, func(s1, s2 string) {})
	require.Nil(t, err)
	require.Equal(t, addr, newAddr)
	res, err := k.QueryERCSingleOutput(ctx, "native", addr, "name")
	require.Nil(t, err)
	require.Equal(t, "test2", res.(string))
	res, err = k.QueryERCSingleOutput(ctx, "native", addr, "symbol")
	require.Nil(t, err)
	require.Equal(t, "test2", res.(string))
	res, err = k.QueryERCSingleOutput(ctx, "native", addr, "decimals")
	require.Nil(t, err)
	require.Equal(t, uint8(12), res.(uint8))
	_, err = k.QueryERCSingleOutput(ctx, "native", addr, "nonexist")
	require.NotNil(t, err)
}
```

**File:** contracts/src/NativeSeiTokensERC20.sol (L7-23)
```text
contract NativeSeiTokensERC20 is ERC20 {

    address constant BANK_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001001;

    string public denom;
    string public nname;
    string public ssymbol;
    uint8 public ddecimals;
    IBank public BankPrecompile;

    constructor(string memory denom_, string memory name_, string memory symbol_, uint8 decimals_) ERC20("", "") {
        BankPrecompile = IBank(BANK_PRECOMPILE_ADDRESS);
        denom = denom_;
        nname = name_;
        ssymbol = symbol_;
        ddecimals = decimals_;
    }
```

**File:** x/tokenfactory/keeper/msg_server.go (L188-217)
```go
func (server msgServer) SetDenomMetadata(goCtx context.Context, msg *types.MsgSetDenomMetadata) (*types.MsgSetDenomMetadataResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Defense in depth validation of metadata
	err := msg.Metadata.Validate()
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Metadata.Base)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	server.bankKeeper.SetDenomMetaData(ctx, msg.Metadata)

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgSetDenomMetadata,
			sdk.NewAttribute(types.AttributeDenom, msg.Metadata.Base),
			sdk.NewAttribute(types.AttributeDenomMetadata, msg.Metadata.String()),
		),
	})

	return &types.MsgSetDenomMetadataResponse{}, nil
}
```

**File:** precompiles/pointer/pointer.go (L99-133)
```go
func (p PrecompileExecutor) AddNative(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	token := args[0].(string)
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
	contractAddr, err := p.evmKeeper.UpsertERCNativePointer(ctx, evm, token, utils.ERCMetadata{Name: name, Symbol: symbol, Decimals: decimals})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}

```

**File:** x/evm/keeper/pointer_upgrade.go (L49-57)
```go
func (k *Keeper) UpsertERCNativePointer(
	ctx sdk.Context, evm *vm.EVM, token string, metadata utils.ERCMetadata,
) (contractAddr common.Address, err error) {
	return k.UpsertERCPointer(
		ctx, evm, "native", []interface{}{
			token, metadata.Name, metadata.Symbol, metadata.Decimals,
		}, k.GetERC20NativePointer, k.SetERC20NativePointer,
	)
}
```

**File:** x/evm/keeper/pointer_upgrade.go (L89-134)
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
```

**File:** x/tokenfactory/keeper/createdenom.go (L12-50)
```go
// CreateDenom creates a new token denom with the given subdenom.
func (k Keeper) CreateDenom(ctx sdk.Context, creatorAddr string, subdenom string) (newTokenDenom string, err error) {
	denom, err := k.validateCreateDenom(ctx, creatorAddr, subdenom)
	if err != nil {
		return "", err
	}

	err = k.createDenomAfterValidation(ctx, creatorAddr, denom)
	return denom, err
}

// Runs CreateDenom logic after the charge and all denom validation has been handled.
// Made into a second function for genesis initialization.
func (k Keeper) createDenomAfterValidation(ctx sdk.Context, creatorAddr string, denom string) (err error) {
	denomMetaData := banktypes.Metadata{
		DenomUnits: []*banktypes.DenomUnit{{
			Denom:    denom,
			Exponent: 0,
		}},
		Base: denom,
		// The following is necessary for x/bank denom validation
		Display: denom,
		Name:    denom,
		Symbol:  denom,
	}

	k.bankKeeper.SetDenomMetaData(ctx, denomMetaData)

	authorityMetadata := types.DenomAuthorityMetadata{
		Admin: creatorAddr,
	}
	err = k.setAuthorityMetadata(ctx, denom, authorityMetadata)
	if err != nil {
		return err
	}

	k.addDenomFromCreator(ctx, creatorAddr, denom)
	return nil
}
```
