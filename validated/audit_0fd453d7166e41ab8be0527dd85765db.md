### Title
Corruptible Pointer-Contract Upgrade via `UpsertERCPointer`/`GetDeploymentCode` Reusing Storage Without Layout Compatibility Checks - ([File: x/evm/keeper/pointer_upgrade.go])

### Summary
The EVM↔CW pointer-upgrade path lets any unprivileged EVM caller (via the Pointer precompile) trigger an in-place "upgrade" of an existing ERC20/ERC721/ERC1155 pointer contract by re-running the new version's constructor bytecode at the *same* contract address and overwriting only the code, while leaving all prior storage slots untouched — with no verification that the old and new pointer bytecode share a compatible storage layout.

### Finding Description
`UpsertERCPointer` in `x/evm/keeper/pointer_upgrade.go` is the generic entry point used by `UpsertERCNativePointer`, `UpsertERCCW20Pointer`, `UpsertERCCW721Pointer`, and `UpsertERCCW1155Pointer`. When a pointer for a given token already exists, it does not redeploy a fresh contract; instead it calls `evm.GetDeploymentCode(evmModuleAddress, bin, suppliedGas, utils.Big0, existingAddr)` to run the *new* pointer bytecode's constructor against the existing address, and then directly overwrites the account's code with `k.SetCode(writeCtx, contractAddr, ret)`: [1](#0-0) 

This is invoked, for example, from the legacy Pointer precompile's `AddCW20`, which unlike the earlier precompile versions (`v552`, `v555`, `v562`, which explicitly reject re-registration when a pointer already exists) calls `UpsertERCCW20Pointer` unconditionally, allowing any unprivileged EVM transaction to trigger this "upsert" flow for an already-registered pointer: [2](#0-1) 

The pointer contracts themselves (`CW20ERC20Pointer.sol`, `CW721ERC721Pointer.sol`, `CW1155ERC1155Pointer.sol`) are plain, non-upgrade-safe Solidity contracts inheriting standard (non-upgradeable) OpenZeppelin `ERC20`/`ERC721` base contracts with immediate constructor-set state, exactly the "corruptible upgradability" pattern described in the reference report: they were never designed with reserved storage gaps or layout versioning guarantees between bytecode versions: [3](#0-2) 

Because `UpsertERCPointer` reuses the existing account's storage across different pointer-contract bytecode versions (identified only by an artifact/version number tracked separately in `x/evm/artifacts/*`), any change to field ordering, added/removed state variables, or changed base-contract composition between pointer versions will silently misalign the new bytecode's storage reads/writes against the stale storage left behind by the previous version — with no gap reservation, no storage-layout diff check, and no wipe of prior slots before the new constructor executes.

### Impact Explanation
If a newer pointer-contract version changes storage layout relative to an older one (e.g., during a chain upgrade that bumps `cw20.CurrentVersion`/`erc721.CurrentVersion`/`erc1155.CurrentVersion` and ships new pointer artifact bytecode), any existing pointer contract silently upgraded through this code path would have its storage reinterpreted incorrectly by the new bytecode. Since pointer contracts mediate CW↔EVM token balances/ownership (ERC20/ERC721/ERC1155 representations of CW20/CW721/CW1155 tokens), a layout mismatch can cause the pointer to read/write the wrong storage slots for balances, allowances, or ownership mappings — leading to permanently corrupted or inaccessible token accounting (fund freezing) or, depending on the exact slot collision, incorrect balance/ownership values that could enable unauthorized transfers through the pointer.

### Likelihood Explanation
The re-upgrade path is reachable from an ordinary EVM transaction that calls the Pointer precompile's `Add*` methods where the "upsert" implementation is wired in (as in `precompiles/pointer/legacy/v66/pointer.go`), i.e., no special privileges are required to trigger a pointer "upgrade" for an already-registered CW token. The actual exploitability is conditioned on validators/governance shipping a new pointer artifact version whose storage layout is incompatible with a prior one — the code base has no automated or manual guard preventing such an incompatible artifact rollout, so the risk is entirely dependent on future pointer contract changes rather than the current shipped artifacts having a known layout mismatch (which I could not confirm by diffing all historical `contracts/src/*Pointer*.sol` bytecode/layouts within the available context).

### Recommendation
- Version pointer contracts' storage layout explicitly (e.g., via an explicit storage struct with reserved gap slots, similar to the OpenZeppelin upgradeable pattern) so future pointer bytecode revisions cannot silently reinterpret prior storage.
- Before calling `GetDeploymentCode`/`SetCode` in `UpsertERCPointer`, verify (at minimum via a version-tagged storage layout hash, or by requiring migrations to explicitly declare compatibility) that the new pointer bytecode's storage layout is compatible with the version currently deployed at `existingAddr`.
- Alternatively, adopt a real proxy pattern (e.g., EIP-1967) for pointer contracts, separating logic (upgradeable) from storage (fixed layout with reserved gaps), instead of overwriting code in place on a fixed EOA-like account.

### Proof of Concept
1. Chain currently has a pointer contract for a CW20 token deployed at address `P` running bytecode version N (`cw20.CurrentVersion` == N), with balances/allowances stored per its layout.
2. A chain upgrade ships pointer artifact version N+1 whose Solidity source reorders or adds new state variables (a plausible and expected way to add pointer functionality, exactly the "adding new variables" scenario from the reference report).
3. Any user calls the Pointer precompile's `AddCW20` (or the upgrade is triggered by `MigrateERCCW20Pointers`/`RunWithOneOffEVMInstance`), invoking `UpsertERCCW20Pointer` → `UpsertERCPointer`.
4. Since `existingAddr` already has a pointer, `evm.GetDeploymentCode` runs the N+1 constructor against `existingAddr`'s live storage (not wiped), and `k.SetCode` swaps in the N+1 bytecode while retaining N's storage.
5. Post-upgrade, the N+1 contract's storage-slot assumptions no longer match the actual stored data, causing incorrect balance/ownership reads for the pointer going forward. [4](#0-3)

### Citations

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

**File:** precompiles/pointer/legacy/v66/pointer.go (L136-166)
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	cwAddress, err := sdk.AccAddressFromBech32(cwAddr)
	if err != nil {
		return nil, 0, err
	}
	res, err := p.wasmdKeeper.QuerySmartSafe(ctx, cwAddress, []byte("{\"token_info\":{}}"))
	if err != nil {
		return nil, 0, err
	}
	formattedRes := map[string]interface{}{}
	if err := json.Unmarshal(res, &formattedRes); err != nil {
		return nil, 0, err
	}
	name := formattedRes["name"].(string)
	symbol := formattedRes["symbol"].(string)
	contractAddr, err := p.evmKeeper.UpsertERCCW20Pointer(ctx, evm, cwAddr, utils.ERCMetadata{Name: name, Symbol: symbol})
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(contractAddr)
	remainingGas = pcommon.GetRemainingGas(ctx, p.evmKeeper)
	return
}
```

**File:** contracts/src/CW721ERC721Pointer.sol (L13-32)
```text
contract CW721ERC721Pointer is ERC721,ERC2981 {

    address constant WASMD_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001002;
    address constant JSON_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001003;
    address constant ADDR_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001004;

    string public Cw721Address;
    IWasmd public WasmdPrecompile;
    IJson public JsonPrecompile;
    IAddr public AddrPrecompile;

    error NotImplementedOnCosmwasmContract(string method);
    error NotImplemented(string method);

    constructor(string memory Cw721Address_, string memory name_, string memory symbol_) ERC721(name_, symbol_) {
        WasmdPrecompile = IWasmd(WASMD_PRECOMPILE_ADDRESS);
        JsonPrecompile = IJson(JSON_PRECOMPILE_ADDRESS);
        AddrPrecompile = IAddr(ADDR_PRECOMPILE_ADDRESS);
        Cw721Address = Cw721Address_;
    }
```
