### Title
Unconditional in-place pointer redeploy corrupts existing pointer contract storage - (File: x/evm/keeper/pointer_upgrade.go)

### Summary
The current (non-legacy) pointer-creation path, `UpsertERCPointer` in `x/evm/keeper/pointer_upgrade.go`, redeploys constructor bytecode directly on top of an already-deployed pointer contract's storage whenever a pointer already exists for a given pointee, with no version gate protecting against re-running a newer/older constructor over the old storage layout. This mirrors the Ribbon "corruptible storage upgradeability pattern" bug class: contract code is replaced (here, effectively an in-place "upgrade" via redeployed init code) while inherited/prior storage is not preserved or protected, so storage slots can be reinterpreted incorrectly.

### Finding Description
`UpsertERCPointer` is the common implementation backing `UpsertERCNativePointer`, `UpsertERCCW20Pointer`, `UpsertERCCW721Pointer`, and `UpsertERCCW1155Pointer` [1](#0-0) . When a pointer already exists for the pointee (`exists == true`), instead of deploying a brand-new contract, it calls `evm.GetDeploymentCode` with the *current* artifact bytecode (`artifacts.GetBin(typ)`) targeted at the **existing** contract address, then overwrites the on-chain code with `k.SetCode`, re-running the constructor logic in place of the old one: [2](#0-1) 

Unlike this generic path, every legacy precompile pointer-creation implementation (`v552`, `v555`, `v562`, etc.) explicitly guards against overwriting a pointer that is already at or above the current pointer version: [3](#0-2) [4](#0-3) 

`UpsertERCPointer`, however, contains no such `existingVersion >= CurrentVersion` check at all — any call for a pointee that already has a pointer unconditionally proceeds to the redeploy-in-place branch. Since pointer contract artifacts are versioned per chain-binary release (`cw20.CurrentVersion`, `cw721.CurrentVersion`, etc., embedded in `x/evm/artifacts/*`), and different versions of the pointer contracts can add or reorder storage variables between releases, redeploying a newer version's constructor directly over an older version's storage layout — without clearing storage or reserving slots — can misinterpret existing storage (e.g., ERC20 pointer allowance/balance-cache mappings, or wrapped-token metadata) under the new layout, corrupting the pointer's local state.

### Impact Explanation
Pointer contracts are the sole EVM-side representation of CW20/CW721/CW1155/native assets; users interact with them for balances, allowances, and transfers. If an in-place redeploy misaligns storage between the old and new constructor's variable layout, funds represented via pointer-local bookkeeping (e.g., ERC20 approve/allowance state used for `transferFrom` flows) can be lost, frozen, or become inconsistent with the underlying CW/native asset, leading to unauthorized transfers or permanently stuck state for holders of that pointer.

### Likelihood Explanation
This path is reached anytime `UpsertERCPointer` is invoked for a pointee that already has a registered pointer — including through migration flows (`x/evm/migrations/migrate_all_pointers.go`) and the general non-legacy precompile pointer-creation entry points that call `Upsert*Pointer`. Because the generic path has no version/no-op guard (in contrast to every hardcoded legacy version), any code path or governance/registration flow that lands on `UpsertERCPointer` for an existing pointer will always perform the risky in-place redeploy, regardless of whether the new bytecode is storage-layout-compatible with what is already deployed.

### Recommendation
Reintroduce an explicit version/no-op check in `UpsertERCPointer` (matching the pattern used in all `precompiles/pointer/legacy/*` implementations) so a redeploy is only performed for the intended, storage-compatible metadata-refresh case, and never silently re-runs a differently-laid-out constructor over live storage. Additionally, require pointer contract artifacts to explicitly manage storage compatibility (e.g., storage gaps or append-only layout) between `CurrentVersion` bumps, and document this constraint next to `artifacts.GetBin`/`CurrentVersion` definitions to prevent regressions.

### Proof of Concept
Not fully verifiable without runtime access: exploiting this requires diffing the actual Solidity/bytecode storage layouts across two `CurrentVersion` values of a given pointer artifact (e.g., `x/evm/artifacts/cw20`) to confirm a slot-shifting change, then showing a call path that reaches `UpsertERCPointer` for an existing pointer without the version guard present in the legacy precompiles. I was unable to locate the current (non-legacy) `precompiles/pointer/pointer.go` `Add*` method bodies within available tool budget to confirm whether they omit the version check found in the legacy versions before calling `UpsertERCCW20Pointer`/`UpsertERCCW721Pointer`/etc. This is the key remaining verification step to fully confirm end-to-end reachability from an unprivileged transaction.

### Citations

**File:** x/evm/keeper/pointer_upgrade.go (L49-87)
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

func (k *Keeper) UpsertERCCW20Pointer(
	ctx sdk.Context, evm *vm.EVM, cw20Addr string, metadata utils.ERCMetadata,
) (contractAddr common.Address, err error) {
	return k.UpsertERCPointer(
		ctx, evm, "cw20", []interface{}{
			cw20Addr, metadata.Name, metadata.Symbol,
		}, k.GetERC20CW20Pointer, k.SetERC20CW20Pointer,
	)
}

func (k *Keeper) UpsertERCCW721Pointer(
	ctx sdk.Context, evm *vm.EVM, cw721Addr string, metadata utils.ERCMetadata,
) (contractAddr common.Address, err error) {
	return k.UpsertERCPointer(
		ctx, evm, "cw721", []interface{}{
			cw721Addr, metadata.Name, metadata.Symbol,
		}, k.GetERC721CW721Pointer, k.SetERC721CW721Pointer,
	)
}

func (k *Keeper) UpsertERCCW1155Pointer(
	ctx sdk.Context, evm *vm.EVM, cw1155Addr string, metadata utils.ERCMetadata,
) (contractAddr common.Address, err error) {
	return k.UpsertERCPointer(
		ctx, evm, "cw1155", []interface{}{
			cw1155Addr, metadata.Name, metadata.Symbol,
		}, k.GetERC1155CW1155Pointer, k.SetERC1155CW1155Pointer,
	)
}
```

**File:** x/evm/keeper/pointer_upgrade.go (L115-134)
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
	} else {
		_, contractAddr, remainingGas, err = evm.Create(evmModuleAddress, bin, suppliedGas, uint256.NewInt(0))
	}
```

**File:** precompiles/pointer/legacy/v562/pointer.go (L180-184)
```go
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw20.CurrentVersion(ctx))
	}
```

**File:** precompiles/pointer/legacy/v555/pointer.go (L211-215)
```go
	cwAddr := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20CW20Pointer(ctx, cwAddr)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, cw20.CurrentVersion(ctx))
	}
```
