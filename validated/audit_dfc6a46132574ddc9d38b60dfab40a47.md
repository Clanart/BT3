I found a strong analog. `MsgAssociateContractAddress` in `x/evm/keeper/msg_server.go` lets any transaction sender permanently bind the EVM-address counterpart of an *arbitrary* CosmWasm contract to that contract's Sei address — with `msg.Sender` never checked against the contract's admin/creator, only that the target is "a wasm contract" and doesn't already have an association.### Title
Permissionless `MsgAssociateContractAddress` lets any account front-run the EVM-address association of an arbitrary CW contract - ([File: x/evm/keeper/msg_server.go])

### Summary
`AssociateContractAddress` in `x/evm/keeper/msg_server.go` accepts any `msg.Sender` and permanently binds the direct-cast EVM address of an arbitrary CosmWasm contract to that contract in the Sei/EVM address-mapping registry, with the only checks being "does a wasm contract exist at this address" and "is it not already associated." There is no check that `msg.Sender` is the contract's admin, creator, or otherwise authorized to trigger this state-changing association for that specific contract.

### Finding Description
`AssociateContractAddress` looks up the contract info for the target address and, if it exists and has no EVM mapping yet, calls `SetAddressMapping` to create a permanent bidirectional Sei↔EVM address association — using nothing but `msg.Address` supplied by the caller: [1](#0-0) 

The message's only sender-side check is `ValidateBasic`, which merely confirms `msg.Sender` and `msg.Address` are well-formed bech32 addresses — it never ties the sender to the target contract: [2](#0-1) 

`SetAddressMapping` itself performs no authorization and unconditionally writes the mapping and event: [3](#0-2) 

This mirrors the reported bug class (`CoreOracle.setRoutes` lacking access control so "any user can set the oracle routes for tokens") in that a critical, address-routing piece of state — which CW contract maps to which EVM address for the entire chain — can be finalized by an unrelated, unprivileged account rather than the contract's owner/admin/deployer.

### Impact Explanation
Once an unprivileged account calls `AssociateContractAddress` for a target CW contract before its rightful owner/deployer does so, the mapping becomes permanent (the same function explicitly rejects re-association: `"contract already has an associated address"`, confirmed by `TestAssociateContractAddress`): [4](#0-3) 

Since this EVM-address association is later relied on to route calls/balances between the CW contract's Sei identity and its EVM identity throughout the EVM subsystem (precompiles, StateDB bridging, etc. use `GetEVMAddress`/`GetSeiAddress` as the address-of-record), any front-running of this call is effectively racing to "claim" the association timing for a contract that the legitimate operator did not yet get to associate. However, because the target must already be a genuine wasm contract instance (`server.wasmViewKeeper.GetContractInfo(ctx, addr) == nil` check) and the mapping simply reflects `common.BytesToAddress(addr)` (a deterministic direct cast, not an attacker-chosen address), the attacker cannot redirect the mapping to an address of their choosing — it always resolves to the same direct-cast EVM address regardless of who calls it. This significantly limits the exploitable impact: the *timing* of the association event is attacker-controllable, but the *resulting mapping value* is not, so no fund redirection or arbitrary address hijack results from this specific function on its own.

### Likelihood Explanation
Any transaction sender can invoke this message against any already-deployed CW contract address at any time before the contract's admin does, at negligible cost, since there is no fee premium or admin gate. This is trivially reachable from a public RPC client submitting a signed transaction. However, given the deterministic-address limitation noted above, the practical value of exploiting the missing access control is unclear — it does not appear to translate into fund loss, permanent freezing, unauthorized transfer, or supply manipulation as required by this analysis's acceptance criteria.

### Recommendation
Add an authorization check to `AssociateContractAddress` (and/or the corresponding `MsgAssociateContractAddress.ValidateBasic`/`GetSigners`) requiring `msg.Sender` to match the target contract's admin (as returned by `wasmViewKeeper.GetContractInfo`) or otherwise be the contract itself acting via `wasmd` sudo/execute, rather than allowing an arbitrary third party to trigger the association.

### Proof of Concept
1. Deploy/observe a CW contract at address `C` with admin `A` (attacker is neither `A` nor `C`).
2. Before `A` submits `MsgAssociateContractAddress{Sender: A, Address: C}`, an unrelated attacker submits `MsgAssociateContractAddress{Sender: attacker, Address: C}`.
3. `AssociateContractAddress` in `x/evm/keeper/msg_server.go` succeeds because it only checks that `C` is a wasm contract and has no existing association — it does not check `msg.Sender`.
4. The mapping is now permanently set (further association attempts, including by `A`, fail with `"contract already has an associated address"`), demonstrating that an unprivileged sender fully controls the timing/execution of an operation intended for the contract's operator.

**Note on confidence**: I was unable to fully trace every downstream consumer of the `GetEVMAddress`/`GetSeiAddress` mapping for CW contracts within the time available, so I cannot conclusively rule out a scenario where controlling *when* this association is set (rather than its value) creates an exploitable window (e.g., a race with a pointer-registration or precompile call that behaves differently based on association state). This would need further investigation using a live Devin session with full repository and test-execution access before treating this as more than a low/informational access-control gap.

### Citations

**File:** x/evm/keeper/msg_server.go (L326-343)
```go
func (server msgServer) AssociateContractAddress(goCtx context.Context, msg *types.MsgAssociateContractAddress) (*types.MsgAssociateContractAddressResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)
	addr := sdk.MustAccAddressFromBech32(msg.Address) // already validated
	// check if address is for a contract
	if server.wasmViewKeeper.GetContractInfo(ctx, addr) == nil {
		return nil, errors.New("no wasm contract found at the given address")
	}
	evmAddr := common.BytesToAddress(addr)
	existingEvmAddr, ok := server.GetEVMAddress(ctx, addr)
	if ok {
		if existingEvmAddr.Cmp(evmAddr) != 0 {
			logger.Error("unexpected associated EVM address exists for contract", "existing", existingEvmAddr, "contract", addr, "expected", evmAddr)
		}
		return nil, errors.New("contract already has an associated address")
	}
	server.SetAddressMapping(ctx, addr, evmAddr)
	return &types.MsgAssociateContractAddressResponse{}, nil
}
```

**File:** giga/deps/xevm/types/message_associate_contract_address.go (L26-49)
```go
func (msg *MsgAssociateContractAddress) GetSigners() []sdk.AccAddress {
	from, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{from}
}

func (msg *MsgAssociateContractAddress) GetSignBytes() []byte {
	return sdk.MustSortJSON(ModuleCdc.MustMarshalJSON(msg))
}

func (msg *MsgAssociateContractAddress) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}

	if _, err := sdk.AccAddressFromBech32(msg.Address); err != nil {
		return sdkerrors.ErrInvalidAddress
	}

	return nil
}
```

**File:** x/evm/keeper/address.go (L10-22)
```go
func (k *Keeper) SetAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Set(types.EVMAddressToSeiAddressKey(evmAddress), seiAddress)
	store.Set(types.SeiAddressToEVMAddressKey(seiAddress), evmAddress[:])
	if !k.accountKeeper.HasAccount(ctx, seiAddress) {
		k.accountKeeper.SetAccount(ctx, k.accountKeeper.NewAccountWithAddress(ctx, seiAddress))
	}
	ctx.EventManager().EmitEvent(sdk.NewEvent(
		types.EventTypeAddressAssociated,
		sdk.NewAttribute(types.AttributeKeySeiAddress, seiAddress.String()),
		sdk.NewAttribute(types.AttributeKeyEvmAddress, evmAddress.Hex()),
	))
}
```

**File:** x/evm/keeper/msg_server_test.go (L808-814)
```go
	// setting for an associated address would fail
	_, err = msgServer.AssociateContractAddress(sdk.WrapSDKContext(ctx), &types.MsgAssociateContractAddress{
		Sender:  dummySeiAddr.String(),
		Address: res.PointerAddress,
	})
	require.NotNil(t, err)
	require.Contains(t, err.Error(), "contract already has an associated address")
```
