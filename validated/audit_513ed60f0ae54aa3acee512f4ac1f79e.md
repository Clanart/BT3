This confirms the finding: `MsgRegisterPointer.ValidateBasic()` only checks that `ErcAddress` is a syntactically valid hex address [1](#0-0) , and `RegisterPointer` in the msg server never checks that the target EVM address currently has contract code — it unconditionally instantiates (or migrates) a CW pointer contract keyed by that raw `ErcAddress` string [2](#0-1) . This is demonstrated directly by the migration test, which successfully registers a pointer for the all-zero placeholder address `0x0000...0000`, which obviously has no contract at all [3](#0-2) .

### Title
Permissionless CW→ERC pointer registration for not-yet-deployed addresses lets an attacker pre-claim the pointer identity of a future contract - (File: x/evm/keeper/msg_server.go)

### Summary
`MsgRegisterPointer` (the `x/evm.MsgServer.RegisterPointer` handler) lets any account register a CosmWasm "pointer" contract for an arbitrary EVM address, without verifying that any contract (let alone an ERC20/721/1155-compliant one) currently exists at that address [4](#0-3) . This mirrors the reported bug class: a privilege/identity binding is created for a name/address reference before the referenced object exists, and the binding silently "activates" for whatever object with that identifier shows up later.

### Finding Description
`ValidateBasic` for `MsgRegisterPointer` only validates that `ErcAddress` is a well-formed hex string via `common.IsHexAddress` [1](#0-0) . The handler `RegisterPointer` then looks up any existing pointer for that address, and if none exists (or the existing one is stale), it unconditionally instantiates a new CW pointer contract with `erc20_address`/`erc721_address`/`erc1155_address` set to the caller-supplied address, and registers it in the `CW20ERC20Pointer`/`CW721ERC721Pointer`/`CW1155ERC1155Pointer` registry keyed by that raw address [5](#0-4) . There is no check via `evmKeeper.GetCode`/`GetCodeHash` that a contract (or specifically an ERC20/721/1155-compatible contract) is deployed at `ErcAddress`. The only gate is the chain-wide `RegisterPointerDisabled` param, not a per-address existence check [6](#0-5) .

Because CW pointer contracts proxy calls (balances, ownership, transfers, royalty queries) directly to whatever bytecode lives at the registered EVM address at call time (not at registration time), an attacker can:
1. Predict or intend to deploy a legitimate-looking ERC20/721/1155 contract at a specific future address (e.g., via a known deployer + nonce, or a CREATE2 factory salt they control).
2. Call `MsgRegisterPointer` today to bind that not-yet-existing address to a CW pointer contract, claiming the pointer "namespace" for that future contract before it exists — exactly analogous to the Neo4j bug where a privilege granted to an unresolved/future-created name silently attaches once that name comes into existence.
3. Later deploy attacker-controlled bytecode to that address (or wait for/front-run a third party's deployment there), after which the already-registered CW pointer — which downstream tooling, bridges, marketplaces or other contracts may already trust as canonical for that "pointee" address — now serves data/control from the attacker's contract.

This also creates a griefing/DoS vector on the CW↔EVM pointer namespace: since only one pointer can exist per (address, type) below the current pointer version (`existingVersion >= currentVersion` gate) [7](#0-6) , an attacker can permanently squat the pointer slot for any EVM address a legitimate project intends to deploy to, preventing the real project's future `RegisterPointer` call from creating a fresh, trustworthy pointer (it would instead just migrate the attacker's pre-existing pointer contract).

### Impact Explanation
This is a form of "incorrect privilege/identity assignment via unresolved-namespace binding": pointer identity intended for a specific (not-yet-existing) contract gets bound in advance and activates automatically when any bytecode later appears at that address. Depending on how the pointer contract is consumed downstream (bridges, marketplaces, other CosmWasm/EVM contracts trusting `getCW20Pointer`/`getNativePointer`-style lookups), this can lead to unauthorized asset representation, spoofed token metadata, or squatting-based denial of legitimate pointer registration for the affected address — impacting fund safety/asset authenticity guarantees that the pointer registry is meant to provide.

### Likelihood Explanation
`MsgRegisterPointer` is a standard, unprivileged, fee-only Cosmos message reachable from any account via `seid tx evm register-cw-pointer` or an equivalent client [8](#0-7) . No special role, whitelist, or existence-check gates it beyond the global `RegisterPointerDisabled` flag, so any transaction sender can pre-register a pointer for an arbitrary address at will.

### Recommendation
Before instantiating/migrating a pointer for `msg.ErcAddress`, require that the target address currently has non-empty code (`evmKeeper.GetCode`/`GetCodeHash` != empty) and, ideally, that it satisfies the expected ERC20/721/1155 interface (mirroring what `AddCW20`/`AddCW721`/`AddNative` do for the reverse direction, which query CW state before creating a pointer). Re-validate this invariant on subsequent Migrate/upgrade paths as well, so a pointer cannot remain bound to an address whose contract has since changed identity.

### Proof of Concept
1. Compute a future contract address `X` you will deploy to (e.g., known deployer + next nonce, or a CREATE2 salt in a factory you control).
2. Before deploying anything at `X`, submit `MsgRegisterPointer{PointerType: ERC20, ErcAddress: X}` from any funded account — this succeeds and creates/registers a CW pointer contract for `X` even though `X` has zero code, exactly as the migration test demonstrates for the zero address [3](#0-2) .
3. Later deploy an ERC20 contract (arbitrary bytecode) to `X`.
4. Any consumer of `GetCW20ERC20Pointer(X)` now resolves to the pointer contract that mirrors whatever was deployed at `X`, without the pointer ever having been validated against real ERC20 semantics at registration time.

### Citations

**File:** x/evm/types/message_register_pointer.go (L47-58)
```go
func (msg *MsgRegisterPointer) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}

	if !common.IsHexAddress(msg.ErcAddress) {
		return sdkerrors.ErrInvalidAddress
	}

	return nil
}
```

**File:** x/evm/keeper/msg_server.go (L247-323)
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
	if err != nil {
		return nil, err
	}
	switch msg.PointerType {
	case types.PointerType_ERC20:
		err = server.SetCW20ERC20Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc20"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc20.CurrentVersion))))
	case types.PointerType_ERC721:
		err = server.SetCW721ERC721Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc721"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc721.CurrentVersion))))
	case types.PointerType_ERC1155:
		err = server.SetCW1155ERC1155Pointer(ctx, common.HexToAddress(msg.ErcAddress), pointerAddr.String())
		ctx.EventManager().EmitEvent(sdk.NewEvent(
			types.EventTypePointerRegistered, sdk.NewAttribute(types.AttributeKeyPointerType, "erc1155"),
			sdk.NewAttribute(types.AttributeKeyPointerAddress, pointerAddr.String()), sdk.NewAttribute(types.AttributeKeyPointee, msg.ErcAddress),
			sdk.NewAttribute(types.AttributeKeyPointerVersion, fmt.Sprintf("%d", erc1155.CurrentVersion))))
	default:
		panic("unknown pointer type")
	}
	return &types.MsgRegisterPointerResponse{PointerAddress: pointerAddr.String()}, err
```

**File:** x/evm/migrations/migrate_all_pointers_test.go (L97-114)
```go
func TestMigrateCWERC721Pointers(t *testing.T) {
	k := testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx([]byte{}).WithBlockTime(time.Now())
	params := k.GetParams(ctx)
	params.RegisterPointerDisabled = false
	k.SetParams(ctx, params)
	require.Nil(t, migrations.StoreCWPointerCode(ctx, &k, false, true, false))
	msgServer := keeper.NewMsgServerImpl(&k)
	res, err := msgServer.RegisterPointer(sdk.WrapSDKContext(ctx), &types.MsgRegisterPointer{
		PointerType: types.PointerType_ERC721,
		ErcAddress:  "0x0000000000000000000000000000000000000000",
	})
	require.Nil(t, err)
	require.Nil(t, migrations.MigrateCWERC721Pointers(ctx, &k))
	// address should stay the same
	addr, _, _ := k.GetCW721ERC721Pointer(ctx, common.HexToAddress("0x0000000000000000000000000000000000000000"))
	require.Equal(t, res.PointerAddress, addr.String())
}
```

**File:** x/evm/client/cli/native_tx.go (L60-87)
```go
func RegisterCwPointerCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "register-cw-pointer [pointer type] [erc address]",
		Short: `Register a CosmWasm pointer for an ERC20/721/1155 contract. Pointer type is either ERC20, ERC721, or ERC1155.`,
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			clientCtx, err := client.GetClientTxContext(cmd)
			if err != nil {
				return err
			}

			msg := &types.MsgRegisterPointer{
				Sender:      clientCtx.GetFromAddress().String(),
				PointerType: types.PointerType(types.PointerType_value[args[0]]),
				ErcAddress:  args[1],
			}
			if err := msg.ValidateBasic(); err != nil {
				return err
			}

			return tx.GenerateOrBroadcastTxCLI(cmd.Context(), clientCtx, cmd.Flags(), msg)
		},
	}

	flags.AddTxFlagsToCmd(cmd)

	return cmd
}
```
