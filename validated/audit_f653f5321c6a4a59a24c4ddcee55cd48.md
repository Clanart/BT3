## Title
`addNativePointer` lets anyone redeploy an ERC20 pointer with new `decimals()` while raw token balances stay unrescaled, corrupting the effective value any integrator computes from the pointer - ([File: precompiles/pointer/pointer.go])

### Summary
The `pointer` precompile's `addNativePointer` method has upsert semantics: any caller can re-invoke it for an already-pointed tokenfactory/bank denom, and it will redeploy the ERC20 pointer contract using whatever `decimals` value is currently stored in the denom's bank metadata, while leaving the underlying raw balances (which are the literal bank-module `usei`-style integer amounts, not decimals-scaled internal ledger entries) completely untouched. This mirrors the reported `PaymentSettler` bug class: accounting (here, the ERC20 `decimals()` exposed by the pointer) is derived once, but the system permits changing the "decimals" basis later without rescaling the existing raw amounts, corrupting every consumer that interprets `balanceOf`/`totalSupply` using `decimals()`.

### Finding Description
`NativeSeiTokensERC20.sol` is a thin ERC20 wrapper deployed by the EVM keeper for a bank/tokenfactory denom: [1](#0-0) 
`balanceOf`/`totalSupply` simply forward to the bank precompile and return the raw integer amount of the denom (e.g. raw `usei`-equivalent units), while `decimals()` returns the immutable-looking `ddecimals` field set at construction time from the denom's `bank` `Metadata`.

The precompile's `AddNative` handler derives `decimals` from the denom's current bank metadata every time it is invoked, and calls `UpsertERCNativePointer`, which is unconditionally an upsert - if a pointer already exists for the token, it redeploys the contract in place (same address, new constructor args) rather than rejecting the call: [2](#0-1) [3](#0-2) [4](#0-3) 

The unit test explicitly confirms decimals can change on redeploy for the same pointer address, with the raw supply (denom `Amount`) never rescaled: [5](#0-4) 

Nothing gates `AddNative` to the denom's admin - it is reachable by any EVM caller, and the integration test explicitly documents "upsert semantics: re-registering the same denom SUCCEEDS": [6](#0-5) 

The denom's bank `Metadata` (which supplies the `decimals` the pointer reads) is separately mutable by the tokenfactory denom admin via `SetDenomMetadata`, with no validation preventing changing the decimals exponent of a token that already has a live ERC20 pointer and non-zero balances: [7](#0-6) 

So the full chain is: (1) tokenfactory denom admin creates a denom with metadata decimals=6, mints/holds a raw balance of e.g. `1_000_000` (i.e. "1.0" token at 6 decimals); (2) anyone calls `addNativePointer` deploying the ERC20 pointer, `decimals()==6`; (3) the admin changes the denom's `SetDenomMetadata` to decimals=18 (or any other value) via `MsgSetDenomMetadata`; (4) anyone re-calls `addNativePointer` for the same denom, which redeploys the ERC20 pointer in place with `decimals()==18`, while `balanceOf` continues to return the exact same raw integer `1_000_000` - now interpreted by every EVM consumer (wallets, DEX routers, price oracles that read `decimals()`) as `0.000000000000001` tokens instead of `1.0` tokens (or the reverse direction, inflating the apparent value by 10^12).

### Impact Explanation
Any EVM-integrated consumer of this pointer contract (DEX pools/pricing math, lending protocols computing collateral value, off-chain wallets/exchanges) reads `decimals()` to interpret the raw integer amount returned by `balanceOf`/`totalSupply`. Because `decimals()` can be silently changed post-deployment while the raw integer amounts are never rescaled, this allows:
- Mispricing on any swap/lending integration built on top of the pointer that trusts `decimals()`, enabling an attacker (who is or colludes with the denom admin) to manipulate perceived token value by orders of magnitude and drain paired liquidity or over-borrow against artificially inflated collateral value — a direct fund-loss vector via price/decimals manipulation.
- Corrupted accounting anywhere `decimals()` is cached/used for computation before and after the change, exactly analogous to the reported `PaymentSettler` decimal-mismatch bug.

### Likelihood Explanation
Requires the denom's tokenfactory admin to change `Metadata.DenomUnits` exponent for a denom that already has an ERC20 pointer, then any unprivileged EVM account can trigger the redeploy via `addNativePointer` (no permission check, publicly documented as "upsert" behavior in the test suite). No governance action or validator collusion is needed — a malicious or compromised tokenfactory denom admin (a normal, permissionless-to-create tokenfactory role) combined with any public caller is sufficient.

### Recommendation
- Make `decimals` immutable once a native ERC20 pointer has been created for a denom: reject `addNativePointer`/`UpsertERCNativePointer` redeploys whose `metadata.Decimals` differs from the value used at first deployment (or store `decimals` in the pointer registry rather than re-deriving it from mutable bank metadata every call).
- Alternatively, reject `MsgSetDenomMetadata` changes to the decimals exponent of a denom that already has a registered EVM/CW pointer, mirroring the fix pattern used for `PaymentSettler` (freeze the accounting basis at first use; require explicit migration/rescaling if it must change).

### Proof of Concept
1. Create tokenfactory denom `factory/{admin}/foo` and register bank `Metadata` with `decimals=6`; mint `1_000_000` raw units to `admin` (displays as `1.0`).
2. Call pointer precompile `addNativePointer("factory/{admin}/foo")` from any account → ERC20 pointer deployed at `P`, `P.decimals()==6`, `P.balanceOf(admin)==1_000_000`.
3. As the tokenfactory admin, call `MsgSetDenomMetadata` to update the same denom's metadata `DenomUnits` exponent to `18` (see [7](#0-6) , which performs no check against existing pointers or balances).
4. Call `addNativePointer("factory/{admin}/foo")` again from any account → same address `P` (upsert semantics, confirmed by [5](#0-4) ), now `P.decimals()==18`, while `P.balanceOf(admin)` is still the untouched raw `1_000_000` — i.e., the same account balance now represents a value ~10^12 times smaller when interpreted by any decimals-aware EVM consumer, without any transfer or mint/burn having occurred.

### Citations

**File:** contracts/src/NativeSeiTokensERC20.sol (L33-43)
```text
    function balanceOf(address account) public view override returns (uint256) {
        return BankPrecompile.balance(account, denom);
    }

    function decimals() public view override returns (uint8) {
        return ddecimals;
    }

    function totalSupply() public view override returns (uint256) {
        return BankPrecompile.supply(denom);
    }
```

**File:** precompiles/pointer/pointer.go (L99-127)
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

**File:** x/evm/keeper/pointer_upgrade.go (L89-141)
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
```

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

**File:** integration_test/precompile_tests/precompiles/pointer.spec.ts (L10-12)
```typescript
 * addNativePointer has upsert semantics: re-registering the same denom
 * SUCCEEDS and keeps the pointer address stable — pinned below; do not add a
 * "duplicate registration reverts" test.
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
