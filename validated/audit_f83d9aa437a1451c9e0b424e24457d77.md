## Analysis

The Nova bug class is: a privileged binding (VNC token ↔ port) survives the deletion of the resource it was issued for, and a new resource that takes over the same slot inherits the stale binding, letting an unrelated party reach it. The closest reachable analog in `sei-chain` is in the **pointer-contract upgrade path** for CW↔EVM pointer contracts.

### Root cause

Any unprivileged EVM caller can invoke the `pointer` precompile's `AddCW20Pointer` / `AddNativePointer` / `AddCW721Pointer` / `AddCW1155Pointer` methods at address `0x...100b` with no authorization check at all: [1](#0-0) [2](#0-1) 

These call into `UpsertERCCW20Pointer`/`UpsertERCNativePointer`/etc., which delegate to `UpsertERCPointer`: [3](#0-2) 

When a pointer for the given pointee (`cwAddr`/`token`) **already exists**, instead of deploying a fresh contract, the keeper computes new deployment bytecode via `evm.GetDeploymentCode(...)` and then simply calls `k.SetCode(writeCtx, contractAddr, ret)` — overwriting the runtime bytecode **at the existing pointer address in place**, while leaving that contract's existing storage completely untouched: [4](#0-3) 

Critically, this "already exists" branch is unconditionally reachable in the current (non-legacy) precompile code — the legacy versions (`v552`, `v555`, `v562`) explicitly rejected re-registration with `if exists && existingVersion >= N { return error }`, but the current `precompiles/pointer/pointer.go` `AddCW20`/`AddNative`/`AddCW721` no longer perform that existence/version check before calling `Upsert...`, so any address can force a re-deployment of an already-registered pointer contract at will: [5](#0-4) 

### Why this matches the Nova bug class

- The pointer contract's on-chain **storage** (which for ERC20/ERC721/ERC1155 pointer templates holds allowance/approval and cached-state slots) is a "slot" that is never cleared/invalidated when the code occupying that address changes.
- Because `AddCW20Pointer`/etc. are callable by **any unprivileged EVM caller**, an attacker can trigger a template upgrade of a pointer contract they don't own, at a time of their choosing, causing the address to run different bytecode (a newer/older `artifacts.GetBin(typ)` template) against storage slots that were written under a different code version's storage layout — exactly analogous to a stale token/binding (approval state bound to "the old VM") being handed to whatever now occupies that slot ("the new VM"), without the original owner's action or consent.
- If any pointer template version changes storage slot layout (adding/removing/reordering state variables) between artifact versions, previously-granted ERC20/ERC721 approvals or other security-relevant state recorded under the old layout could be misinterpreted by the new code, potentially enabling unauthorized transfer authority to persist or be reinterpreted incorrectly — i.e., unauthorized transfer via a pointer contract, one of the explicitly in-scope impacts.

### Recommendation
Re-introduce and enforce the existence/version guard removed from the current `precompiles/pointer/pointer.go` `AddCW20`/`AddNative`/`AddCW721`/`AddCW1155` (as still present in the legacy versions), and/or require that `UpsertERCPointer`'s in-place `SetCode` path only fire when explicitly invoked by a governance/whitelisted upgrade path, never from an arbitrary unauthenticated EVM caller. Additionally, any pointer-template code upgrade that changes storage layout should also clear/re-initialize the contract's storage rather than only overwriting code.

### Caveat
I could not find direct evidence within the indexed code that any current pointer-template version (`artifacts.GetBin("cw20"/"cw721"/...)`) actually differs in storage layout across versions, nor confirmed a concrete exploitable state-confusion instance — that would require inspecting the compiled pointer-template bytecode/ABI history across versions, which is outside what the index surfaces. Given the strict "no low/informational/speculative" bar in the prompt and the missing concrete confirmation of a storage-layout mismatch that causes fund loss, I can't assert this rises to a fully proven High/Critical finding with certainty — it is a real permissionless code-overwrite path but its funds-impact depends on template storage-layout facts I couldn't verify from the indexed files. If you want, a Devin session with full repo access could diff the `artifacts` pointer template bytecode/ABI across versions to confirm whether a storage-layout mismatch actually exists.

### Citations

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

**File:** precompiles/pointer/pointer.go (L134-164)
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

**File:** precompiles/pointer/legacy/v562/pointer.go (L173-184)
```go
func (p PrecompileExecutor) AddCW20(ctx sdk.Context, method *ethabi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}
	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw20.CurrentVersion(ctx))
	}
```
