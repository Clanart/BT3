## Analog Found

### Title
Tokenfactory denom admin can mutate an already-deployed ERC20 native pointer's immutable `decimals()` post-integration, enabling value-miscalculation theft in composing DeFi contracts - (File: `precompiles/pointer/pointer.go`, `x/evm/keeper/pointer_upgrade.go`, `x/tokenfactory/keeper/msg_server.go`)

### Summary
This mirrors the reported bug class: an on-chain actor is allowed to change an economic parameter (price/currency in the original report; ERC20 `decimals()` here) of an asset *after* other parties have already committed funds/logic based on the original value, letting the actor drain value from integrating counterparties.

### Finding Description
A tokenfactory denom's admin (any unprivileged account that calls `MsgCreateDenom` becomes admin of that denom) can call `MsgSetDenomMetadata` at any time with no lifecycle restriction to change the denom's `DenomUnits`/exponent (i.e., its decimal precision), gated only by an ownership check, not by whether the denom already has downstream integrations: [1](#0-0) 

Separately, the `pointer` precompile's `addNativePointer` method is **permissionless** — any EVM caller can invoke it for any denom with bank metadata, and it has explicit **upsert semantics**: calling it again for the same denom redeploys the pointer contract's runtime code at the *same* address using the *current* bank denom metadata: [2](#0-1) 

The redeploy path re-runs the constructor via `evm.GetDeploymentCode` against the existing contract address and overwrites its runtime bytecode/storage with values derived from the fresh metadata: [3](#0-2) 

The deployed native pointer contract stores `decimals` as a plain constructor-set state variable (`ddecimals`), which `decimals()` returns and which the ERC-20 spec/most integrators assume is immutable for the life of the token: [4](#0-3) 

This exact redeploy-with-different-decimals scenario is explicitly exercised by the test suite, confirming the mutation is a real, reachable state transition rather than theoretical: [5](#0-4) 

### Impact Explanation
Just as the reported bug lets a project owner change the raise's price/currency after presale funds are committed (stealing tokens/ETH from other raises), here a tokenfactory denom admin can:
1. Create a tokenfactory denom and set initial metadata (e.g., 6 decimals).
2. Call `addNativePointer` to deploy the ERC20 pointer at address `X` with `decimals() == 6`.
3. Get the pointer integrated by a DEX/lending/vault contract that reads and caches `decimals()` to price the token or compute collateral ratios, and get users/counterparties to deposit funds or open positions against that pricing.
4. Call `MsgSetDenomMetadata` to change the exponent (e.g., to 18) and re-invoke `addNativePointer`, which redeploys pointer `X` in place with the new decimals baked in via `GetDeploymentCode`.
5. Any downstream logic that assumed `decimals()` immutability now mis-prices balances by orders of magnitude (10^12 in this example), letting the admin extract disproportionate value from AMMs/lending pools/counterparties — a direct unauthorized transfer of funds via a Cosmos precompile/pointer, matching the "concrete fund loss ... via precompile or pointer" acceptance criterion.

### Likelihood Explanation
Both preconditions are reachable by a fully unprivileged actor with a single tokenfactory denom and normal EVM transactions: creating a tokenfactory denom (`MsgCreateDenom`) is permissionless, `MsgSetDenomMetadata` only requires being that denom's self-appointed admin, and `addNativePointer` has no caller restriction whatsoever. No governance, validator, or operator privileges are required.

### Recommendation
Freeze the pointer's economic parameters once external value has been committed against it: either (a) disallow re-upserting a native/CW pointer's metadata (name/symbol/decimals) once created — require a brand-new pointer address for decimal changes instead of overwriting the existing address's bytecode/storage — or (b) require `MsgSetDenomMetadata` to reject decimal-exponent changes for denoms that already have a registered ERC20 pointer, forcing decimals to be immutable once externally observable, analogous to locking Raise/Tier parameters once `presaleStart` (fund commitment) has begun.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/attacker/foo`, then `MsgSetDenomMetadata` to set `DenomUnits` exponent = 6.
2. Attacker (or anyone) calls `pointer.addNativePointer("factory/attacker/foo")` — deploys `NativeSeiTokensERC20` at address `P` with `ddecimals = 6` [6](#0-5) .
3. A victim DeFi contract integrates `P`, reading `decimals()==6` to price deposits; users deposit `foo` tokens and/or the attacker supplies `foo` as collateral valued at 6-decimal precision.
4. Attacker calls `MsgSetDenomMetadata` again, changing the exponent to 18, then re-calls `pointer.addNativePointer("factory/attacker/foo")`. Per the upsert path, `UpsertERCPointer` calls `evm.GetDeploymentCode` against the existing address `P`, overwriting stored `ddecimals` to 18 [7](#0-6) .
5. `P.decimals()` now returns 18 while the underlying bank-module balances (raw integer amounts) are unchanged — any contract that computes value as `amount / 10**decimals()` now under- or over-values the attacker's balance by a factor of 10^12, allowing the attacker to borrow/withdraw disproportionate value or manipulate an AMM pool price, extracting funds from the integrating protocol/counterparties.

### Citations

**File:** x/tokenfactory/keeper/msg_server.go (L188-206)
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
```

**File:** precompiles/pointer/pointer.go (L99-132)
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

**File:** contracts/src/NativeSeiTokensERC20.sol (L7-39)
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

    function name() public view override returns (string memory) {
        return nname;
    }

    function symbol() public view override returns (string memory) {
        return ssymbol;
    }

    function balanceOf(address account) public view override returns (uint256) {
        return BankPrecompile.balance(account, denom);
    }

    function decimals() public view override returns (uint8) {
        return ddecimals;
    }
```

**File:** x/evm/keeper/pointer_upgrade_test.go (L73-126)
```go
// TestUpsertERCNativePointerKeepsCodeCacheCoherent covers the mid-tx redeploy
// path: plant a warm memo, re-upsert (exists → keeper SetCode + RefreshCodeCache),
// and assert GetCode follows the store.
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
