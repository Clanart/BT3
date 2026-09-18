### Title
Unprivileged re-invocation of `AddNativePointer`/`AddCW20Pointer`/`AddCW721Pointer`/`AddCW1155Pointer` allows anyone to silently redeploy a live pointer contract with new metadata (decimals/name/symbol) - ([File: precompiles/pointer/pointer.go])

### Summary
The current `pointer` precompile's `AddNative`, `AddCW20`, `AddCW721`, and `AddCW1155` methods are callable by **any** unprivileged EVM caller with no admin/owner gate and, critically, no check that a pointer already exists at the target denom/CW address. They forward directly into `Keeper.UpsertERCPointer`, which — if a pointer already exists — redeploys new bytecode over the *same* pointer contract address using metadata queried live at call time. This is the same bug class as the reported "arbitrary `finishUpgrade` replay": a state-defining operation (contract (re)initialization) can be invoked repeatedly and arbitrarily by anyone, resetting the "upgraded" contract to a new state without any distinguishing signal.

### Finding Description
`AddNative` (and the CW20/721/1155 equivalents) in [1](#0-0)  has no `existingVersion`/`exists` guard (unlike several legacy versions such as v552/v555/v562/v600 which explicitly reject re-registration via `if exists && existingVersion >= 1 { return ... error }`, see [2](#0-1) ). The current, latest-version implementation removed that guard and instead calls `p.evmKeeper.UpsertERCNativePointer` directly with freshly queried metadata: [3](#0-2) 

`UpsertERCPointer`, the shared keeper implementation, explicitly supports the "already exists" branch by redeploying the constructor code at the *existing* contract address via `evm.GetDeploymentCode(..., existingAddr)` and calling `k.SetCode`, effectively rerunning the constructor over live state: [4](#0-3) 

The pointer contract's immutable-looking fields (`nname`, `ssymbol`, `ddecimals`) are simply constructor parameters written on every deployment, with no protection against being re-set: [5](#0-4) [6](#0-5) 

Because `AddNative`'s metadata is derived live from `bankKeeper.GetDenomMetaData` at call time (see lines 107–123 of `pointer.go`), any change to a denom's on-chain metadata (e.g., by the tokenfactory denom's admin/creator changing `decimals` via a `MsgSetDenomMetadata`-style call) can be "baked in" to the existing pointer address the next time *anyone* calls `AddNativePointer` for that denom — silently changing what `ERC20.decimals()` (and `name()`/`symbol()`) returns for an address that other contracts (DEXes, lending markets, price oracles) already integrated against, assuming immutability.

The event emitted on redeploy, `EventTypePointerRegistered`, is identical to the event emitted on first registration [7](#0-6) , so there is no way to distinguish "a pointer was just created" from "an existing pointer's constructor was silently re-run with different values" — mirroring exactly the "no event to signal replay" complaint in the external report.

### Impact Explanation
If a downstream DeFi protocol (AMM, lending market, price oracle) has already cached or hardcoded assumptions about a native/CW20/CW721/CW1155 pointer's `decimals`, `name`, or `symbol` (all of which are legitimately treated as immutable per ERC20/ERC721 conventions), a subsequent unprivileged call to `AddNativePointer`/`AddCW20Pointer`/etc. for the same denom/address can change those return values in place without redeploying to a new address. A decimals change alone (e.g. from 6 to 18 or vice versa) is a well-known DeFi attack primitive that can cause massive miscalculation of swap amounts, collateral valuation, or liquidation thresholds in any integrated contract that read `decimals()` once and cached it, or that assumes decimals never change for a given address — leading to direct fund loss/drain in those integrations.

### Likelihood Explanation
- Reachable by any transaction sender / EVM caller: the precompile has no `onlyOwner`/admin check.
- The precondition (changing a denom's stored metadata after a pointer already exists) is directly reachable by any tokenfactory denom creator/admin for their own denom via the standard bank denom-metadata update path — no governance or validator privilege required.
- No version/existence guard is present in the current `pointer.go` (this guard exists in earlier legacy versions, indicating a regression), making exploitation straightforward once the precondition is met.
- The action is otherwise indistinguishable from a routine, expected pointer registration in the emitted events, decreasing detectability.

### Recommendation
Reinstate an existence/version guard in `AddNative`, `AddCW20`, `AddCW721`, and `AddCW1155` in `precompiles/pointer/pointer.go` so a pointer cannot be silently "re-upserted" by an arbitrary caller once created (mirroring the legacy `existingVersion >= N` checks). If intentional pointer upgrades to a newer code version are needed, gate them behind an explicit version bump check (as the legacy code already does) rather than unconditional re-deployment, and emit a distinct event (e.g. `pointer_upgraded` vs `pointer_registered`) so metadata changes on an existing pointer are auditable on-chain.

### Proof of Concept
1. Tokenfactory denom `factory/creator/mytoken` is created with bank denom metadata `decimals=6`.
2. Anyone calls the `pointer` precompile's `addNativePointer("factory/creator/mytoken")`; `UpsertERCNativePointer` creates a new ERC20 pointer contract `P` with `decimals()==6`. A DEX pool or lending market integrates `P`, caching `decimals=6`.
3. The denom creator/admin updates the denom's bank metadata to `decimals=18` (via the standard, permissionless-for-owner tokenfactory metadata update flow).
4. Any unprivileged account calls `addNativePointer("factory/creator/mytoken")` again. Because there is no existence/version guard in [8](#0-7) , `UpsertERCPointer`'s "exists" branch runs and redeploys the constructor at the same address `P` with `decimals=18` [9](#0-8) .
5. `P.decimals()` now returns 18 for the same address the integrated DEX/lending market still treats as 6-decimal, causing a 10^12x miscalculation in any downstream swap/collateral logic that assumed immutable decimals — resulting in fund loss for that integration.

### Citations

**File:** precompiles/pointer/pointer.go (L99-131)
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
```

**File:** precompiles/pointer/legacy/v562/pointer.go (L122-126)
```go
	token := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20NativePointer(ctx, token)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, native.CurrentVersion)
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

**File:** x/evm/keeper/pointer_upgrade.go (L142-144)
```go
	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, typ),
		sdk.NewAttribute(types.AttributeKeyPointerAddress, contractAddr.Hex()), sdk.NewAttribute(types.AttributeKeyPointee, pointee)))
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

**File:** contracts/src/NativeSeiTokensERC20.sol (L37-39)
```text
    function decimals() public view override returns (uint8) {
        return ddecimals;
    }
```
