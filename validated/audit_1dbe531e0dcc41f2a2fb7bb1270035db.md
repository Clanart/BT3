### Title
Pointer contract in-place upgrade reuses storage without layout compatibility guarantees, enabling storage collision on native/CW20/CW721/CW1155 pointer upgrades - (File: `x/evm/keeper/pointer_upgrade.go`)

### Summary
`UpsertERCPointer` upgrades an existing pointer contract by overwriting the bytecode at its existing address in place, without resetting or migrating the storage that was written by the prior version's constructor/initializer. This mirrors the storage-collision root cause in the referenced report: a "new implementation" is attached to an address whose storage was laid out by an older implementation, and there is no mechanism enforcing that the two layouts are compatible.

### Finding Description
When a pointer for a native denom, CW20, CW721, or CW1155 token already exists and needs to be upgraded to a newer version of the pointer artifact, `k.UpsertERCPointer` does not deploy a new contract at a new address. Instead it calls `evm.GetDeploymentCode` to run the new version's constructor logic, then directly overwrites the code at the pre-existing `contractAddr` via `k.SetCode(writeCtx, contractAddr, ret)`: [1](#0-0) 

This pattern is architecturally identical to a Solidity storage-layout upgrade: the "old" pointer contract (e.g. `x/evm/artifacts/native`, `cw20`, `cw721`, `cw1155`) already occupies storage slots at `contractAddr` from its original constructor run (e.g. `denom`, `name`, `symbol`, `decimals` public state variables in `NativeSeiTokensERC20`/`CW20ERC20Pointer`-style contracts, as seen in `contracts/src/NativeSeiTokensERC20.sol` and `contracts/src/CW20ERC20Pointer.sol`): [2](#0-1) [3](#0-2) 

If a future version of these pointer artifacts (`native.CurrentVersion`, `cw20`'s versioned artifacts, etc.) reorders, removes, or adds storage variables relative to the prior version deployed at that address (analogous to inserting `kernel`/`isKeycodeHighRisk` between `GovernorBravoDelegateStorageV2` and `GovernorBravoDelegate` in the referenced Compound-derived bug), the constructor run via `GetDeploymentCode` writes new variables into slots that still hold stale data from the old layout, or the new getter functions (`decimals()`, `balanceOf()`, etc.) read slots whose semantics changed between versions. There is no explicit storage-layout compatibility check, migration step, or storage-clearing step before the in-place code swap; the code only checks version numbers for gating re-registration (`existingVersion >= CurrentVersion`), not for storage compatibility, as seen in the legacy pointer precompile executors: [4](#0-3) 

The reused-address pattern is confirmed by the regression test comment "address should stay the same as before" after an artificial version downgrade/upgrade: [5](#0-4) 

### Impact Explanation
If any future pointer-artifact version bump changes storage variable ordering/types without an explicit migration of the underlying slots, every already-deployed pointer contract for that token type would return corrupted `name`/`symbol`/`decimals`/`totalSupply`/`balanceOf` data or, in the worst case, misinterpret leftover storage as a different address/config that could be leveraged for unauthorized transfers or accounting corruption in the ERC20/ERC721/ERC1155 pointer, causing fund-accounting inconsistencies for any EVM/public-RPC user interacting with that pointer. Given pointer contracts are user-facing bridges between CW and EVM assets, corrupted state directly affects balance/allowance reads reachable by any EVM transaction sender.

### Likelihood Explanation
This is not exploitable by an external caller today with the current single generation of artifacts — it requires the *chain operators* to ship a future pointer-artifact version whose storage layout is incompatible with the prior one, at which point the in-place `SetCode` upgrade path in `UpsertERCPointer` would silently reuse incompatible storage for every existing pointer of that type. Since the codebase does not include a storage-layout audit/versioning guard in the upgrade path itself (only application-level version-number gating, not slot-compatibility validation), there is no protection against introducing this exact class of bug in a subsequent artifact revision. This makes it a latent architectural weakness rather than a currently triggerable exploit with the code present in this snapshot.

### Recommendation
Add an explicit storage-layout compatibility contract (or a per-version storage migration step) to `UpsertERCPointer`/`GetDeploymentCode` so that upgrading pointer bytecode in place either (a) deploys new pointer versions at fresh addresses instead of overwriting existing storage, or (b) enforces/asserts that the new artifact's storage layout is a strict superset/append-only extension of every prior version's layout before allowing the `SetCode` overwrite, with automated tooling (e.g., storage-layout diffing in CI for `x/evm/artifacts/*`) to catch layout regressions before they ship.

### Proof of Concept
Not directly reproducible against the current codebase because only one storage layout exists per pointer artifact today; the vulnerability would only manifest once a second, storage-incompatible version of a given pointer artifact (native/cw20/cw721/cw1155) is introduced and `UpsertERCPointer` is invoked to upgrade an existing pointer, at which point:
1. Deploy pointer v1 for a token via `addNativePointer`/`addCW20Pointer` (existing flow in `precompiles/pointer/pointer.go`).
2. Ship pointer artifact v2 with a changed storage variable order/type.
3. Call the upgrade path again so `UpsertERCPointer` hits the `exists` branch and calls `k.SetCode(writeCtx, contractAddr, ret)` at `x/evm/keeper/pointer_upgrade.go:121-131`, reusing v1's storage slots for v2's constructor and reads, producing corrupted `balanceOf`/`totalSupply`/`decimals` results for the pointer's existing token holders.

### Citations

**File:** x/evm/keeper/pointer_upgrade.go (L115-131)
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

**File:** contracts/src/CW20ERC20Pointer.sol (L11-27)
```text
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

**File:** precompiles/pointer/legacy/v552/pointer.go (L145-149)
```go
	token := args[0].(string)
	existingAddr, existingVersion, exists := p.evmKeeper.GetERC20NativePointer(ctx, token)
	if exists && existingVersion >= 1 {
		return nil, 0, fmt.Errorf("pointer at %s with version %d exists when trying to set pointer for version %d", existingAddr.Hex(), existingVersion, native.CurrentVersion)
	}
```

**File:** precompiles/pointer/pointer_test.go (L80-92)
```go
	// upgrade to a newer version
	// hacky way to get the existing version number to be below CurrentVersion
	testApp.EvmKeeper.DeleteERC20NativePointer(statedb.Ctx(), "test", version)
	testApp.EvmKeeper.SetERC20NativePointerWithVersion(statedb.Ctx(), "test", pointerAddr, version-1)
	statedb = state.NewDBImpl(statedb.Ctx(), &testApp.EvmKeeper, true)
	evm = vm.NewEVM(*blockCtx, statedb, cfg, vm.Config{}, testApp.EvmKeeper.CustomPrecompiles(ctx))
	_, _, err = p.RunAndCalculateGas(evm, caller, caller, append(p.GetExecutor().(*pointer.PrecompileExecutor).AddNativePointerID, args...), suppliedGas, nil, nil, false, false)
	require.Nil(t, err)
	require.Nil(t, statedb.GetPrecompileError())
	newAddr, _, exists := testApp.EvmKeeper.GetERC20NativePointer(statedb.Ctx(), "test")
	require.True(t, exists)
	require.Equal(t, addr, pointerAddr)
	require.Equal(t, newAddr, pointerAddr) // address should stay the same as before
```
