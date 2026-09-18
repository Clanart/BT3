### Title
In-place ERC-pointer "upgrade" reuses old bytecode's storage layout without any migration/reset, corrupting pointer state across versions - ([File: x/evm/keeper/pointer_upgrade.go])

### Summary
The Solidity-side analog of the reported "corruptible upgradeability" issue is not the OpenZeppelin proxy pattern (sei-chain does not use it for production contracts), but `Keeper.UpsertERCPointer`, which performs a de-facto in-place contract upgrade for EVM pointer contracts (`native`, `cw20`, `cw721`, `cw1155`) by overwriting the deployed bytecode at a fixed address while leaving all previously-written storage slots untouched.

### Finding Description
When a pointer for a CW20/CW721/CW1155/native asset already exists, `UpsertERCPointer` does not redeploy a fresh contract at a new address; instead it keeps `contractAddr = existingAddr` and calls `evm.GetDeploymentCode(...)` to obtain the new version's runtime bytecode, then directly overwrites the code at the *existing* address via `k.SetCode(writeCtx, contractAddr, ret)`: [1](#0-0) 

This is functionally equivalent to an unsafe UUPS/proxy upgrade: the address (and therefore all EVM storage slots previously written by the old contract's constructor/state, e.g. ERC20/ERC721 `_balances`, `_allowances`, `_owners`, pointer-specific fields such as `Cw20Address`/`Cw721Address`, precompile handle variables, etc.) is preserved, but the new bytecode is generated from the artifact `bin` for the *new* version of the same-named contract (`x/evm/artifacts/cw20`, `cw721`, `cw1155`, `native`), whose current version numbers are explicitly tracked and bumped over time: [2](#0-1) 

Unlike standard OpenZeppelin upgradeable patterns, these pointer contracts (`contracts/src/CW20ERC20Pointer.sol`, `CW721ERC721Pointer.sol`) are not written with any storage-gap or namespaced-storage convention — they're plain non-upgradeable OpenZeppelin `ERC20`/`ERC721` contracts: [3](#0-2) 

If a future pointer-contract version changes the order, size, or number of state variables (a normal Solidity evolution, e.g. adding a new precompile handle, a new mapping, or reordering inherited OZ base contracts), `SetCode` at the *same* storage address means the new bytecode's compiler-assigned slot layout will silently read stale data left by the old layout (or vice versa) instead of the fresh values passed to the "new" constructor args that were packed into `bin`. There is no explicit storage clearing, no slot compatibility check, and no storage gap reservation before this operation.

This is reachable by any user in two ways:
1. Indirectly, whenever a chain upgrade bumps `currentVersion` for a pointer type and the keeper migration re-invokes `UpsertERCCW20Pointer`/`UpsertERCCW721Pointer`/etc. for every existing pointer (`x/evm/migrations/migrate_all_pointers.go`), which drives execution straight into the vulnerable code path.
2. Directly, since `AddCW20`/`AddNative`/etc. precompile methods (reachable from any EVM transaction) call into the same `UpsertERCPointer` upgrade branch whenever `exists` is true and version checks permit re-registration. [4](#0-3) 

### Impact Explanation
If storage layouts diverge between pointer-contract versions (which is expected/likely as pointer contracts evolve, similar to how the report describes storage-gap risk for upgradeable contracts), the in-place code swap can cause the new pointer contract to read/write wrong storage slots. Depending on which fields collide, this can manifest as: balances/allowances for the wrapped CW20/CW721/CW1155 token being misreported or corrupted for all holders of that pointer, permanently freezing or misdirecting token accounting reachable by any EVM user interacting with that pointer contract (`balanceOf`, `transfer`, `approve`, `ownerOf`, etc.), which maps to concrete fund-loss/freezing impact for the pointer's users.

### Likelihood Explanation
The bug is not a hypothetical proxy defect but an actual mechanism already present and exercised on every version bump of a pointer-contract artifact (there is no test harness validating storage-layout compatibility between `currentVersion` bumps in `x/evm/artifacts/cw20`, `cw721`, `cw1155`, `native`). Because migrations to bump pointer versions are a normal, expected chain-upgrade operation (as seen from the version-tracking constants and `MigrateERCCW20Pointers`/`MigrateERCCW721Pointers` migration functions), any future contract change that adds/reorders state variables in the pointer Solidity source will trigger this on the very next upgrade migration, affecting every registered pointer of that type chain-wide.

### Recommendation
Treat pointer-contract version bumps as unsafe in-place upgrades and require an explicit storage-compatibility contract: either (a) always redeploy pointers at a fresh address on version change (never reuse `existingAddr`+`SetCode`), or (b) adopt an actual upgradeable-proxy pattern with storage gaps/namespaced storage (as already prototyped in the load-generator's `Allowlisted1967Proxy` + slot-namespaced `*Storage.layout()` pattern) so that new pointer-contract versions cannot alias old storage slots, and add CI checks (e.g. slot-layout diff) before allowing a pointer artifact version bump.

### Proof of Concept
Not independently reproducible from static review alone: constructing an actual storage collision requires diffing the compiled storage layout between two `cw20`/`cw721` artifact versions (e.g. `x/evm/artifacts/cw20` at `currentVersion=1` vs `currentVersion=2`), which is outside what static code search can confirm. The exploitable mechanism itself — reuse of `existingAddr` with a raw `SetCode` overwrite and no storage reset — is confirmed directly in `x/evm/keeper/pointer_upgrade.go` lines 108-134, and is triggered end-to-end by `x/evm/migrations/migrate_all_pointers.go`'s `MigrateERCCW20Pointers`/`MigrateERCCW721Pointers`/`MigrateERCCW1155Pointers` functions during any pointer-version chain upgrade.

### Citations

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

**File:** x/evm/artifacts/cw20/artifacts.go (L18-29)
```go
const currentVersion uint16 = 2

var versionOverride uint16

// SetVersionWithOffset allows for overriding the version for integration test scenarios
func SetVersionWithOffset(offset int16) {
	// this allows for negative offsets to mock lower versions
	versionOverride = uint16(int16(currentVersion) + offset) //nolint:gosec
}

func CurrentVersion(ctx sdk.Context) uint16 {
	return config.GetVersionWthDefault(ctx, versionOverride, currentVersion)
```

**File:** contracts/src/CW20ERC20Pointer.sol (L1-27)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/utils/Strings.sol";
import {IWasmd} from "./precompiles/IWasmd.sol";
import {IJson} from "./precompiles/IJson.sol";
import {IAddr} from "./precompiles/IAddr.sol";

contract CW20ERC20Pointer is ERC20 {

    address constant WASMD_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001002;
    address constant JSON_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001003;
    address constant ADDR_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001004;

    string public Cw20Address;
    IWasmd public WasmdPrecompile;
    IJson public JsonPrecompile;
    IAddr public AddrPrecompile;

    constructor(string memory Cw20Address_, string memory name_, string memory symbol_) ERC20(name_, symbol_) {
        WasmdPrecompile = IWasmd(WASMD_PRECOMPILE_ADDRESS);
        JsonPrecompile = IJson(JSON_PRECOMPILE_ADDRESS);
        AddrPrecompile = IAddr(ADDR_PRECOMPILE_ADDRESS);
        Cw20Address = Cw20Address_;
    }
```

**File:** x/evm/migrations/migrate_all_pointers.go (L55-87)
```go
func MigrateERCCW20Pointers(ctx sdk.Context, k *keeper.Keeper) error {
	iter := prefix.NewStore(ctx.KVStore(k.GetStoreKey()), append(types.PointerRegistryPrefix, types.PointerERC20CW20Prefix...)).ReverseIterator(nil, nil)
	defer func() { _ = iter.Close() }()
	seen := map[string]struct{}{}
	for ; iter.Valid(); iter.Next() {
		cwAddr := string(iter.Key()[:len(iter.Key())-2]) // last two bytes are version
		if _, ok := seen[cwAddr]; ok {
			continue
		}
		seen[cwAddr] = struct{}{}
		addr := common.BytesToAddress(iter.Value())
		oName, err := k.QueryERCSingleOutput(ctx, "cw20", addr, "name")
		if err != nil {
			logger.Error("Failed to upgrade pointer due to failed name query", "pointer", cwAddr, "err", err)
			continue
		}
		oSymbol, err := k.QueryERCSingleOutput(ctx, "cw20", addr, "symbol")
		if err != nil {
			logger.Error("Failed to upgrade pointer due to failed symbol query", "pointer", cwAddr, "err", err)
			continue
		}
		_ = k.RunWithOneOffEVMInstance(ctx, func(e *vm.EVM) error {
			_, err := k.UpsertERCCW20Pointer(ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx)), e, cwAddr, utils.ERCMetadata{
				Name:   oName.(string),
				Symbol: oSymbol.(string),
			})
			return err
		}, func(s1, s2 string) {
			logger.Error("Failed to upgrade pointer at step", "pointer", cwAddr, "from-step", s1, "to-step", s2)
		})
	}
	return nil
}
```
