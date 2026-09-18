### Title
Pointer contract version upgrades overwrite bytecode in place while preserving old storage, with no storage-layout compatibility guarantee - (File: x/evm/keeper/pointer_upgrade.go)

### Summary
Sei's ERC20/ERC721/ERC1155 "pointer" contracts (`CW20ERC20Pointer.sol` and the CW→ERC wasm/solidity counterparts) are plain, non-upgrade-safe Solidity contracts — constructor-initialized `ERC20`/`ERC721` implementations with no storage gaps, no proxy, no `Initializable` pattern. Despite this, `Keeper.UpsertERCPointer` in `x/evm/keeper/pointer_upgrade.go` implements an in-place "upgrade" mechanism: when a pointer for a given pointee already exists, instead of deploying a fresh contract at a new address, it computes the new version's deployment code via `evm.GetDeploymentCode` and then directly overwrites the bytecode at the *existing* contract address with `k.SetCode(writeCtx, contractAddr, ret)`, while leaving all of that address's existing storage slots untouched. [1](#0-0) 

### Finding Description
This is the same root cause as the Sherlock M-1 report — mixing an upgrade path with contracts that were never designed to be storage-layout-safe across versions — but expressed through Sei's pointer-versioning mechanism rather than an OZ transparent/UUPS proxy.

`CW20ERC20Pointer` (and the analogous native/erc721/erc1155 pointer contracts) are ordinary constructor-based contracts inheriting OpenZeppelin's non-upgradeable `ERC20`: [2](#0-1) 

There is no `__gap` reservation, no upgradeable-safe base contracts anywhere in the pointer artifacts, and grepping the repo for storage-gap patterns turns up nothing — confirming these contracts were built assuming immutable, single-deployment storage layouts.

Yet `UpsertERCPointer` treats "pointer already exists" as an upgrade case: it re-runs the new version's init/constructor code via `GetDeploymentCode` against the *existing* address and overwrites only the code (`SetCode`), never resetting or migrating storage: [3](#0-2) 

This path is reachable by any ordinary user: `MsgRegisterPointer` triggers `UpsertERCPointer` whenever the on-chain registered pointer version is behind the code's `CurrentVersion` (as demonstrated by `TestRegisterPointer`, which manually rolls back a stored pointer version and shows a subsequent `RegisterPointer` call re-triggers the upgrade path and keeps the same contract address): [4](#0-3) 

Because the underlying Solidity contracts were not written with any storage-compatibility discipline (no gaps, no versioned storage layout guarantees, direct state variables like `Cw20Address`, `WasmdPrecompile`, ERC20's internal `_balances`/`_allowances`/`_totalSupply` mappings at fixed slots), any future pointer-artifact version that adds, removes, reorders, or changes the type of state variables (including switching/reordering inherited base contracts) will read/write mismatched slots against the pre-existing storage left over from the prior version. This is functionally identical to the reported bug class: an "upgrade" happening across incompatible storage layouts, except here it's done via direct code replacement at a live address rather than delegatecall-proxy pattern, arguably worse since there is no gap convention at all to reason about compatibility.

### Impact Explanation
If a future pointer version changes the storage layout (a change entirely plausible since `CurrentVersion` is already incremented over time, e.g., erc20 artifacts at version 2), upgrading in place would corrupt live token accounting for every holder of that pointer token: balances (`_balances` mapping), allowances, and total supply could be misread or overwritten with garbage/stale values inherited from the old layout, or overwritten by the new constructor's writes hitting recycled slots. This is a fund-loss / unauthorized-transfer-class impact (mismatched balance accounting) that affects any user holding pointer-token balances, not just the transaction sender who triggered the upgrade.

### Likelihood Explanation
The trigger (`MsgRegisterPointer`) is callable by any unprivileged account and fires automatically whenever the deployed pointer's stored version lags the binary's `CurrentVersion` — this is the normal, expected upgrade flow for pointer contracts, not an edge case. The vulnerability is latent today only because the current versions of `CW20ERC20Pointer.sol` and sibling contracts happen not to have changed storage layout across the version bumps recorded in `x/evm/artifacts/*/artifacts.go`; there is no compile-time or runtime check preventing a future version bump from breaking this invariant, and no storage-gap/layout-versioning safety net exists to catch it.

### Recommendation
Either (a) stop reusing the same contract address/storage across pointer versions — always deploy new pointer implementations at fresh addresses and migrate the pointee→pointer mapping, or (b) if in-place upgrades are intentional, adopt an explicit storage-layout compatibility contract for all pointer artifacts (reserved storage gaps, a fixed/versioned storage struct with append-only fields, and automated layout-diff checks between artifact versions before any `CurrentVersion` bump is merged).

### Proof of Concept
1. Deploy `CW20ERC20Pointer` v1 for a CW20 token; its constructor sets `Cw20Address`, `name`, `symbol` and inherited `ERC20` storage is untouched (pointer contracts proxy balance queries to the CW20 wasm contract, but future versions could plausibly cache balances locally, as `ERC20`'s internal mappings already exist in storage layout).
2. Bump `CurrentVersion` in `x/evm/artifacts/erc20/artifacts.go` (or the relevant erc721/erc1155/native package) for a new pointer implementation that adds a new state variable before/interleaved with existing ones, or reorders inherited contracts.
3. Any user calls `MsgRegisterPointer` for the same pointee — `x/evm/keeper/msg_server.go` → `UpsertERCPointer` detects the existing (older-version) pointer and takes the "exists" branch: `evm.GetDeploymentCode` + `k.SetCode(writeCtx, contractAddr, ret)` [5](#0-4) .
4. The new bytecode now reads/writes storage slots at the address using the new layout, while the address's storage still holds values written under the old layout — any slot whose meaning changed between versions now returns corrupted data (e.g., an address-typed variable now read as balance amount, or vice versa), corrupting token accounting for that pointer.

### Citations

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

**File:** x/evm/keeper/msg_server_test.go (L580-593)
```go
	// upgrade ERC20 pointer
	k.DeleteCW20ERC20Pointer(ctx, pointee, version)
	k.SetCW20ERC20PointerWithVersion(ctx, pointee, pointer.String(), version-1)
	res, err = keeper.NewMsgServerImpl(k).RegisterPointer(sdk.WrapSDKContext(ctx), &types.MsgRegisterPointer{
		Sender:      sender.String(),
		PointerType: types.PointerType_ERC20,
		ErcAddress:  pointee.Hex(),
	})
	require.Nil(t, err)
	newPointer, version, exists := k.GetCW20ERC20Pointer(ctx, pointee)
	require.True(t, exists)
	require.Equal(t, erc20.CurrentVersion, version)
	require.Equal(t, newPointer.String(), res.PointerAddress)
	require.Equal(t, newPointer.String(), pointer.String()) // should retain the existing contract address
```
