Found a concrete analog. This confirms `RegisterPointer` accepts an arbitrary `msg.ErcAddress` (e.g. `0x0000000000000000000000000000000000000000`) with no validation that it is an actual ERC20/721/1155 contract, and the CW20 pointer contract then blindly issues `DelegateCallEvm { to: erc_addr, ... }` for transfers — the analog of `safeTransferIn()`'s missing contract-existence check.

### Title
CW20/CW721/CW1155→ERC pointer registration and transfer accept non-contract/invalid ERC addresses, causing silent no-op transfers - (File: x/evm/keeper/msg_server.go, example/cosmwasm/cw20/src/contract.rs)

### Summary
`RegisterPointer` in `x/evm/keeper/msg_server.go` instantiates a CW20/CW721/CW1155 pointer contract for an arbitrary caller-supplied `msg.ErcAddress` without verifying that address actually contains ERC20/721/1155 contract code. Once such a pointer is registered, the pointer's `transfer`/`transfer_from` handlers issue an EVM delegate-call to that address; if the address has no code (or code that doesn't implement the expected function), the low-level call returns success with empty return data, exactly the "safeTransferIn silent failure" class described in the reference report.

### Finding Description
`RegisterPointer` accepts `msg.ErcAddress` directly and only checks for an existing pointer version before instantiating the wasm pointer contract via `server.wasmKeeper.Instantiate` / `Migrate`: [1](#0-0) 
There is no call to check `StateDB.GetCode(ercAddress)` or otherwise validate that the target address is a genuine ERC20/721/1155 contract before wiring up the pointer. This is demonstrated directly in the migration test, which successfully registers a pointer for the zero address `0x0000000000000000000000000000000000000000` — an address with no code: [2](#0-1) 

Once instantiated, the CW20 pointer contract's `transfer`/`transfer_from` entry points build an `EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload }` message and dispatch it without checking whether the call actually executed any token-contract logic: [3](#0-2) 

This mirrors the report's root cause precisely: a delegate/low-level call to an address that either has no code, or has code that doesn't match the expected ABI, returns `success=true` with empty return data in the EVM. Nothing downstream (the CW20 contract logic, nor the pointer's Solidity-side `_execute`/`delegatecall` wrapper in `CW20ERC20Pointer.sol`) treats an empty/no-op result as failure: [4](#0-3) 

### Impact Explanation
Any user (or the pointer-creation flow itself) can register a pointer against an address that has no code or a mismatched interface. Subsequent `transfer`/`transferFrom` calls on that pointer will appear to "succeed" (return `true` / emit no revert) while never moving any tokens on the target address, because the underlying delegatecall to a non-contract address is a no-op that still reports success. Downstream integrators (DEXs, wallets, bridges) that rely on the pointer's return value or absence of a revert to confirm a transfer occurred could be misled into believing tokens moved when they did not — leading to accounting desynchronization and potential fund-loss scenarios for any protocol built atop the pointer (e.g. crediting a counterparty for a transfer that silently no-op'd).

### Likelihood Explanation
`RegisterPointer` is a permissionless message reachable by any unprivileged EVM/Cosmos transaction sender (subject to `RegisterPointerDisabled` param), and the address-validity gap is proven by the existing test using the zero address as `ErcAddress` without any registration failure. This makes the precondition for the bug trivially reachable.

### Recommendation
In `RegisterPointer` (`x/evm/keeper/msg_server.go`), before instantiating/migrating the wasm pointer, use the EVM `StateDB`/`vm.EVM.StateDB.GetCodeSize` (or an equivalent live keeper call) to verify `msg.ErcAddress` has non-empty code, and additionally perform an ERC165/interface probe (e.g., attempt `balanceOf`/`totalSupply` for ERC20, or the appropriate `ownerOf`/`balanceOf` selector probes for ERC721/1155) so pointers can only be created against contracts that implement the expected interface. Additionally, harden the CW20/CW721/CW1155 pointer contracts' EVM call wrappers to bubble up failures/empty-return conditions as explicit reverts rather than treating any non-reverting call as success.

### Proof of Concept
1. Call `MsgRegisterPointer{PointerType: ERC20, ErcAddress: "0x0000000000000000000000000000000000000000"}` (or any address with no deployed code) — this succeeds, as shown by `TestMigrateCWERC20Pointers`, which registers a pointer against the zero address without any revert.
2. Execute `transfer` on the resulting CW20 pointer; it issues `DelegateCallEvm` to the zero address, which the EVM reports as a successful, no-op call.
3. The pointer contract returns success/emits no error, even though no ERC20 state at `erc_addr` was touched — a silent failure identical in class to the reported `safeTransferIn()` issue.

### Citations

**File:** x/evm/keeper/msg_server.go (L247-297)
```go
func (server msgServer) RegisterPointer(goCtx context.Context, msg *types.MsgRegisterPointer) (*types.MsgRegisterPointerResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	if server.GetRegisterPointerDisabled(ctx) {
		return nil, fmt.Errorf("registering CW->ERC pointers has been disabled")
	}
	var existingPointer sdk.AccAddress
	var existingVersion uint16
	var currentVersion uint16
	var exists bool
	switch msg.PointerType {
	case types.PointerType_ERC20:
		currentVersion = erc20.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC721:
		currentVersion = erc721.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	case types.PointerType_ERC1155:
		currentVersion = erc1155.CurrentVersion
		existingPointer, existingVersion, exists = server.GetCW1155ERC1155Pointer(ctx, common.HexToAddress(msg.ErcAddress))
	default:
		panic("unknown pointer type")
	}
	if exists && existingVersion >= currentVersion {
		return nil, fmt.Errorf("pointer %s already registered at version %d", existingPointer.String(), existingVersion)
	}
	payload := map[string]interface{}{}
	switch msg.PointerType {
	case types.PointerType_ERC20:
		payload["erc20_address"] = msg.ErcAddress
	case types.PointerType_ERC721:
		payload["erc721_address"] = msg.ErcAddress
	case types.PointerType_ERC1155:
		payload["erc1155_address"] = msg.ErcAddress
	default:
		panic("unknown pointer type")
	}
	codeID := server.GetStoredPointerCodeID(ctx, msg.PointerType)
	moduleAcct := server.accountKeeper.GetModuleAddress(types.ModuleName)
	var err error
	var pointerAddr sdk.AccAddress
	if exists {
		bz, _ := json.Marshal(map[string]interface{}{})
		pointerAddr = existingPointer
		_, err = server.wasmKeeper.Migrate(ctx, existingPointer, moduleAcct, codeID, bz)
	} else {
		bz, jerr := json.Marshal(payload)
		if jerr != nil {
			return nil, jerr
		}
		pointerAddr, _, err = server.wasmKeeper.Instantiate(ctx, codeID, moduleAcct, moduleAcct, bz, fmt.Sprintf("Pointer of %s", msg.ErcAddress), sdk.NewCoins())
	}
```

**File:** x/evm/migrations/migrate_all_pointers_test.go (L78-95)
```go
func TestMigrateCWERC20Pointers(t *testing.T) {
	k := testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now())
	params := k.GetParams(ctx)
	params.RegisterPointerDisabled = false
	k.SetParams(ctx, params)
	require.Nil(t, migrations.StoreCWPointerCode(ctx, &k, true, false, false))
	msgServer := keeper.NewMsgServerImpl(&k)
	res, err := msgServer.RegisterPointer(sdk.WrapSDKContext(ctx), &types.MsgRegisterPointer{
		PointerType: types.PointerType_ERC20,
		ErcAddress:  "0x0000000000000000000000000000000000000000",
	})
	require.Nil(t, err)
	require.Nil(t, migrations.MigrateCWERC20Pointers(ctx, &k))
	// address should stay the same
	addr, _, _ := k.GetCW20ERC20Pointer(ctx, common.HexToAddress("0x0000000000000000000000000000000000000000"))
	require.Equal(t, res.PointerAddress, addr.String())
}
```

**File:** example/cosmwasm/cw20/src/contract.rs (L227-274)
```rust
fn transfer(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_payload(recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", info.sender)
        .add_attribute("to", recipient)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}

pub fn transfer_from(
    deps: DepsMut<EvmQueryWrapper>,
    _env: Env,
    info: MessageInfo,
    owner: String,
    recipient: String,
    amount: Uint128,
) -> Result<Response<EvmMsg>, ContractError> {
    deps.api.addr_validate(&owner)?;
    deps.api.addr_validate(&recipient)?;

    let erc_addr = ERC20_ADDRESS.load(deps.storage)?;

    let querier = EvmQuerier::new(&deps.querier);
    let payload = querier.erc20_transfer_from_payload(owner.clone(), recipient.clone(), amount)?;
    let msg = EvmMsg::DelegateCallEvm { to: erc_addr, data: payload.encoded_payload };
    let res = Response::new()
        .add_attribute("from", owner)
        .add_attribute("to", recipient)
        .add_attribute("by", info.sender)
        .add_attribute("amount", amount)
        .add_message(msg);

    Ok(res)
}
```

**File:** contracts/src/CW20ERC20Pointer.sol (L98-109)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw20Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```
