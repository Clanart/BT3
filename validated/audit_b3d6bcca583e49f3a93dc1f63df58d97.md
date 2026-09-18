## Confirmed analog found

### Title
Redeploying an ERC20 native pointer with new decimals after `MsgSetDenomMetadata` corrupts decimals for a token already integrated by external contracts - (File: `precompiles/pointer/pointer.go`, `x/evm/keeper/pointer_upgrade.go`, `x/tokenfactory/keeper/msg_server.go`)

### Summary
The Sherlock report shows a "set token" routine that computes downstream state (`total - claimed`) assuming the old and new token share the same decimals, causing a revert/break when decimals differ. The same bug class — an unprivileged actor changing a token's decimals *after* other on-chain logic has already been built around the original decimals — is reachable in sei-chain through the tokenfactory `MsgSetDenomMetadata` message combined with the public, permissionless `addNativePointer` precompile method.

### Finding Description
1. A tokenfactory denom creator is automatically the denom's admin and can call `MsgSetDenomMetadata` at any time to change the denom's `DenomUnits` (including the exponent/decimals field) with no restriction other than being the current admin: [1](#0-0) 

2. Any user (unprivileged, no ownership check) can call the `addNativePointer` EVM precompile method for that denom. The precompile pulls the *current* denom metadata and picks the decimals from it: [2](#0-1) 
This method has no caller-authorization requirement beyond disallowing static/delegate calls: [3](#0-2) 

3. `UpsertERCNativePointer`/`UpsertERCPointer` redeploys the pointer contract bytecode **at the same, already-registered contract address** whenever a pointer already exists for the denom — it does not skip re-registration or block a decimals change for an existing pointer: [4](#0-3) 

4. `NativeSeiTokensERC20` stores `decimals` as an immutable constructor argument (`ddecimals`), but `balanceOf`/`totalSupply` always return the *raw* bank-keeper integer amount for the denom — they are never scaled by decimals: [5](#0-4) 

Putting this together: a tokenfactory denom admin can (a) create a denom and deploy its native ERC20 pointer with decimals = 6 (typical), (b) let other EVM contracts/integrations (AMM pools, lending markets, price oracles) build positions and pricing logic around that fixed 6-decimals ERC20 representation, and then (c) call `MsgSetDenomMetadata` to change the exponent to, say, 18, and (d) re-invoke `addNativePointer` (public/permissionless) to redeploy the pointer at the same address with `decimals() == 18` while `balanceOf` continues to return the same unscaled integer values. Any downstream contract that reads `decimals()` to interpret `balanceOf`/`transfer` amounts (virtually all ERC20-integrating DeFi contracts, e.g. AMM pools computing price = reserve0/reserve1 adjusted by decimals) will now misinterpret existing balances by a factor of 10^12, exactly analogous to the reported "SetToken" bug where a decimals change breaks arithmetic that assumed the old decimals.

### Impact Explanation
This is exploitable for direct fund loss: an attacker who controls a tokenfactory denom deploys the ERC20 pointer, seeds liquidity/positions in an AMM or lending pool assuming e.g. 6 decimals, then flips the denom's decimals via `MsgSetDenomMetadata` + re-triggers `addNativePointer`. Any contract that reads `decimals()` afterward to price/scale balances (which are unchanged raw integers) will compute value off by orders of magnitude, allowing drainage of paired assets from AMM pools or under-collateralized borrowing from lending markets that integrate the pointer as a standard ERC20. This is unauthorized fund extraction via a pointer contract, matching the accepted impact categories (fund loss / unauthorized transfer via pointer).

### Likelihood Explanation
Both preconditions are reachable by ordinary users with no elevated privileges: `MsgCreateDenom`/`MsgSetDenomMetadata` are permissionless tokenfactory operations available to any account (the creator is auto-admin), and `addNativePointer` has no caller restriction. No governance or validator collusion is required — a single malicious tokenfactory denom creator, plus one transaction to change metadata and one to re-invoke the pointer precompile, suffices.

### Recommendation
- Make decimals immutable for a denom once an ERC20 native pointer has been registered for it (reject `MsgSetDenomMetadata` changes to `DenomUnits`/exponent for denoms that already have a `GetERC20NativePointer` entry), or
- Make `UpsertERCNativePointer` refuse to redeploy/alter decimals for an existing pointer (only allow name/symbol updates, never decimals), and/or
- Emit a clear, monitorable event and require the redeploy to go through the same governance-gated proposal path as previously used for `AddERCNativePointerProposal`, rather than the permissionless precompile path, whenever decimals differ from the currently deployed pointer's decimals.

### Proof of Concept
1. Attacker calls `MsgCreateDenom` to create `factory/attacker/token`.
2. Attacker (as admin) calls `MsgSetDenomMetadata` setting `DenomUnits` with exponent 6, mints supply, and calls `addNativePointer` precompile to deploy the ERC20 pointer with `decimals()==6` [2](#0-1) .
3. Attacker seeds an AMM pool (e.g. Uniswap-fork on Sei EVM) pairing the pointer token against `usei`/USDC, with liquidity and price computed assuming 6 decimals.
4. Attacker calls `MsgSetDenomMetadata` again, changing the exponent to 18 [1](#0-0) .
5. Attacker (or anyone) calls `addNativePointer` again; `UpsertERCPointer` redeploys the same pointer address with the new `decimals=18` constructor arg [6](#0-5) , while `balanceOf` values remain unchanged raw integers [5](#0-4) .
6. Any pool/protocol re-reading `decimals()` (or newly deployed pools) now misprices the token by 10^12x relative to the pool's actual raw-integer reserves, letting the attacker arbitrage/drain the paired asset.

### Citations

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

**File:** precompiles/pointer/pointer.go (L72-93)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *ethabi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if readOnly {
		return nil, 0, errors.New("cannot call pointer precompile from staticcall")
	}
	if ctx.EVMPrecompileCalledFromDelegateCall() {
		return nil, 0, errors.New("cannot delegatecall pointer")
	}

	switch method.Name {
	case AddNativePointer:
		return p.AddNative(ctx, method, caller, args, value, evm, hooks)
	case AddCW20Pointer:
		return p.AddCW20(ctx, method, caller, args, value, evm, hooks)
	case AddCW721Pointer:
		return p.AddCW721(ctx, method, caller, args, value, evm, hooks)
	case AddCW1155Pointer:
		return p.AddCW1155(ctx, method, caller, args, value, evm, hooks)
	default:
		err = fmt.Errorf("unknown method %s", method.Name)
	}
	return
}
```

**File:** precompiles/pointer/pointer.go (L99-118)
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
