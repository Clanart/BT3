### Title
In-place bytecode replacement of CW20/CW721/CW1155 ERC pointer contracts via `SetCode` upgrades storage layout without any compatibility check or storage gaps - ([File: x/evm/keeper/pointer_upgrade.go])

### Summary
The Biconomy finding warns that `SmartAccount.sol` inherits from stateful base contracts (`ModuleManager.sol`, etc.) with no storage gaps, so a future upgrade that adds storage to those bases can silently collapse/shift the derived contract's storage layout. Sei's ERC pointer contracts (`CW20ERC20Pointer`, `CW721ERC721Pointer`, `CW1155ERC1155Pointer`) are functionally analogous "upgradeable" contracts: `Keeper.UpsertERCPointer` upgrades an existing pointer **in place** by directly overwriting its runtime bytecode at the same address with `k.SetCode`, while the account's storage (persisted at that address) is left untouched. None of these pointer contracts, nor the OpenZeppelin bases they inherit (`ERC20`, `ERC721`, `ERC2981`), reserve storage gaps, and there is no validation that the new artifact's storage layout is compatible with the old one before the swap.

### Finding Description
`Keeper.UpsertERCPointer` is the shared implementation behind `UpsertERCNativePointer`, `UpsertERCCW20Pointer`, `UpsertERCCW721Pointer`, and `UpsertERCCW1155Pointer`. When a pointer for a given token/contract already exists, instead of deploying a fresh contract, it calls `evm.GetDeploymentCode` to get new runtime bytecode for the current artifact version and then directly patches the existing contract account with `k.SetCode(writeCtx, contractAddr, ret)`: [1](#0-0) 

This mirrors exactly the risk pattern in the Biconomy report: a contract's bytecode/logic is swapped while its already-persisted storage slots remain, and the new logic's variable layout is simply assumed to still line up with whatever was written under the old layout. In Solidity's inheritance-based storage model, this assumption breaks whenever:
- the pointer contract itself gains/loses/reorders state variables (`Cw20Address`, `WasmdPrecompile`, `JsonPrecompile`, `AddrPrecompile`) as seen in `CW20ERC20Pointer.sol`: [2](#0-1) 
- or the inherited OpenZeppelin bases change layout across compiler/OZ-library version bumps, as with `CW721ERC721Pointer is ERC721, ERC2981`: [3](#0-2) 

None of these contracts declare `__gap` reserved storage slots, and `RegisterPointer`/`UpsertERCPointer` performs no on-chain check that the new artifact's storage layout is a superset/compatible extension of the old one — it only checks a numeric `existingVersion` gate (`existingVersion >= N`) before allowing the overwrite, as seen in the legacy precompile executors (e.g. `AddCW20`/`AddNative` version checks) and in `TestRegisterPointer`, which explicitly exercises this same-address in-place upgrade path: [4](#0-3) [5](#0-4) 

Because `Cw20Address`/`Cw721Address` and the three precompile-address fields (`WasmdPrecompile`, `JsonPrecompile`, `AddrPrecompile`) are the *only* persistent state these pointer contracts hold (balances/ownership are proxied live via CosmWasm queries rather than stored locally), a version bump that reorders these fields, or that adds new fields ahead of them in a future pointer contract revision, would read stale/garbage values out of the old storage slots for the fields that shifted position — for example `Cw20Address` could decode as the address of `AddrPrecompile` from a prior layout, causing every subsequent CosmWasm query issued through `WasmdPrecompile.query(Cw20Address, ...)` to target the wrong CW20/CW721/CW1155 contract or to fail/query garbage data.

### Impact Explanation
If a future artifact bump changes the storage layout of these pointer contracts (adding a field, reordering inherited bases, or bumping the OpenZeppelin version pinned in `go.mod`/npm package for the Solidity sources) without corresponding care, every already-deployed pointer contract that gets upgraded in place via `RegisterPointer`/`UpsertERCPointer` would have its core identity fields (`Cw20Address`/`Cw721Address`/precompile addresses) silently corrupted. Since all balance/transfer/ownership operations on the pointer are proxied to whatever CW20/CW721/CW1155 contract address is stored in that slot, a corrupted address could redirect transfers, balance queries, and approvals to an unrelated CosmWasm contract — leading to fund loss (ERC20/721/1155 calls executing against the wrong underlying token/NFT contract) or a permanent freeze of the pointer (queries reverting because `Cw20Address` decodes to an invalid bech32/contract address). This is reachable by any user simply calling the public `RegisterPointer` message once a chain upgrade ships a pointer artifact whose Solidity storage layout diverges from the previous one, with no way to recover other than another gov-mediated pointer redeploy.

### Likelihood Explanation
This is not a currently-exploitable-with-today's-artifacts bug (the current CW20/CW721/CW1155 pointer artifacts across the version history appear to keep the same field ordering), so likelihood of an *immediate* practical exploit is low. However, the structural weakness matches the analog directly: the pointer contracts are stateful, inherit from stateful OpenZeppelin bases with no storage gaps, and are "upgraded" via raw bytecode overwrite (`SetCode`) rather than via a proxy pattern with explicit storage-layout versioning/migration, or via full redeploy-to-new-address semantics. Any future protocol change that adds fields to these contracts (e.g., to support new pointer features) risks silently corrupting every live pointer's identity storage, and the risk is entirely internal to the Sei team's own Solidity source maintenance discipline — there is no on-chain guard preventing it.

### Recommendation
- Add explicit storage gaps (or, better, use unstructured/namespaced storage slots via `keccak256` constants, as already done in this codebase's own load-generator fixtures, e.g. `ProxyERC20Storage`/`LendingStorage` patterns) to `CW20ERC20Pointer`, `CW721ERC721Pointer`, `CW1155ERC1155Pointer`, and any OpenZeppelin base they inherit.
- Before calling `k.SetCode` in `UpsertERCPointer`, validate that the new artifact's storage layout is a strict, compatible extension of the previous version's layout (e.g., via a storage-layout manifest checked at build/CI time and asserted against the on-chain `existingVersion`), or re-emit/re-write the identity fields (`Cw20Address`, `WasmdPrecompile`, `JsonPrecompile`, `AddrPrecompile`) immediately after the code swap rather than assuming they survive intact.
- Consider switching pointer upgrades to a proper proxy pattern (fixed storage proxy + separate implementation contract) so identity state is never subject to layout drift from logic-contract bytecode swaps.

### Proof of Concept
1. Assume a future chain upgrade ships a new pointer Solidity artifact for `cw20` that adds a new state variable before `Cw20Address` (e.g., a `feeRecipient` address) or upgrades the pinned OpenZeppelin `ERC20` dependency to a version with an added storage slot in the inherited base.
2. Any user calls `MsgRegisterPointer{PointerType: ERC20, ErcAddress: <existing_pointee>}` (reachable exactly as exercised in `TestRegisterPointer`) against an already-registered CW20 pointer.
3. `UpsertERCCW20Pointer` → `UpsertERCPointer` detects `exists == true`, calls `evm.GetDeploymentCode` for the new bytecode, and calls `k.SetCode(writeCtx, contractAddr, ret)` — swapping the contract's code at the same address while its previously-stored `Cw20Address`/`WasmdPrecompile`/`JsonPrecompile`/`AddrPrecompile` slots remain from the old layout: [1](#0-0) 
4. All subsequent calls to the pointer (`balanceOf`, `transfer`, `transferFrom`, `approve`) read `Cw20Address` from what is now the *wrong* storage slot per the new layout, causing CosmWasm queries/executes to target an unintended contract address or a garbage bech32 string, resulting in failed transactions (permanent freeze of the pointer) or, in the worst case, operations executed against a different, attacker-influenced CW20 contract if the misaligned slot happens to decode into a valid but unintended address.

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

**File:** x/evm/keeper/msg_server_test.go (L635-648)
```go
	// upgrade ERC721 pointer
	k.DeleteCW721ERC721Pointer(ctx, pointee, version)
	k.SetCW721ERC721PointerWithVersion(ctx, pointee, pointer.String(), version-1)
	res, err = keeper.NewMsgServerImpl(k).RegisterPointer(sdk.WrapSDKContext(ctx), &types.MsgRegisterPointer{
		Sender:      sender.String(),
		PointerType: types.PointerType_ERC721,
		ErcAddress:  pointee.Hex(),
	})
	require.Nil(t, err)
	newPointer, version, exists = k.GetCW721ERC721Pointer(ctx, pointee)
	require.True(t, exists)
	require.Equal(t, erc721.CurrentVersion, version)
	require.Equal(t, newPointer.String(), res.PointerAddress)
	require.Equal(t, newPointer.String(), pointer.String()) // should retain the existing contract address
```
