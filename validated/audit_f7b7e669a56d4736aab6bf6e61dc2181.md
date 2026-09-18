### Title
Address-association precompile allows overwriting an EVM address's canonical Sei mapping without invalidating the stale reverse mapping - (File: precompiles/addr/addr.go, x/evm/keeper/address.go)

### Summary
Similar to the FraxLend bug where `setTimeLock` could reset a value that should be immutable because no "already set" mutex was enforced, the Sei `addr` precompile's `associateAddresses` only checks that the **Sei** address side of a new association is unused, never that the **EVM** address side is already canonically bound to a different Sei address. `SetAddressMapping` itself blindly overwrites the forward key without clearing any older reverse key that pointed at the same EVM address, so the intended one-to-one EVM<->Sei bijection can be silently broken by a second `associate`/`associatePubKey` call.

### Finding Description
`associateAddresses` in the `addr` precompile only guards against re-associating an already-associated **Sei** address: [1](#0-0) 

It never checks `p.evmKeeper.GetSeiAddress(ctx, evmAddr)` to see whether `evmAddr` is already canonically bound to some other Sei address. The underlying `SetAddressMapping` then unconditionally overwrites both directions of the mapping with no reconciliation of any pre-existing reverse pointer: [2](#0-1) 

The codebase's own test comments acknowledge that an EVM address can first receive a "mutable direct-cast mapping" (established automatically for any signer at the ante-handler level, e.g. `UpdateSigners` calling `evmKeeper.SetAddressMapping(ctx, signer, evmAddr)` using the direct-cast Sei address) and that "a later `associatePubKey` could remap" it to the pubkey-derived "true" Sei address: [3](#0-2) [4](#0-3) 

When this remap happens, `EVMAddressToSeiAddressKey(evmAddr)` is updated to the new (true) Sei address, but `SeiAddressToEVMAddressKey(castSeiAddr)` — the old, cast-address forward pointer created earlier — is never deleted, because `associateAddresses`'s uniqueness check only inspects the new Sei address, not the EVM address being reassigned. The keeper's own `DeleteAddressMapping` function shows that removing a mapping requires touching *both* directions, confirming they are not automatically kept consistent when only one direction is rewritten by `SetAddressMapping`: [5](#0-4) 

This leaves the store in an inconsistent state where `GetEVMAddress(ctx, castSeiAddr)` still returns `evmAddr` (stale, "already associated" in one direction) while `GetSeiAddress(ctx, evmAddr)` now returns the new true Sei address — violating the bijective invariant the address-association design is built on, comparable to `TIME_LOCK_ADDRESS` being reset in FraxLend because no "already set" flag/mutex was checked before overwriting.

### Impact Explanation
Multiple parts of the protocol (fee/refund routing, `CanAddressReceive` bank-transfer gating, ERC20/ERC721 pointer and precompile lookups, EVM state migrations) assume `GetEVMAddress`/`GetSeiAddress` form a consistent bijection. A stale forward pointer surviving a remap can cause code paths that key off the old cast address (e.g. balance/fee accounting tied to `SeiAddressToEVMAddressKey`) to diverge from code paths keying off the canonical reverse pointer, creating a class of consistency bugs where funds or association-dependent state resolve to different addresses in different contexts.

### Likelihood Explanation
Reaching this requires only two ordinary, permissionless EVM transactions from the same key: one plain transaction to trigger the ante-handler direct-cast `SetAddressMapping`, followed by an `associatePubKey`/`associate` call for the same key — both fully within reach of an unprivileged EOA, no special privileges needed.

### Recommendation
In `associateAddresses`, before calling `AssociateAddresses`, also check `p.evmKeeper.GetSeiAddress(ctx, evmAddr)`; if it returns an existing, different Sei address, either reject the call or have `SetAddressMapping` explicitly delete the stale reverse/forward keys for the old association (mirroring `DeleteAddressMapping`) before writing the new one, so the EVM<->Sei mapping is enforced as a true bijection rather than silently overwritten.

### Proof of Concept
1. Generate `victimKey`; compute `victimEVM = PubkeyToAddress(victimKey)` and `victimCast = sdk.AccAddress(victimEVM[:])`.
2. Submit any ordinary Cosmos-signed tx from `victimCast`/`victimKey` so the ante handler's `UpdateSigners` calls `evmKeeper.SetAddressMapping(ctx, victimCast, victimEVM)` (see `app/ante/cosmos_checktx.go` lines 530-560), establishing `victimCast -> victimEVM` and `victimEVM -> victimCast`.
3. Call the `addr` precompile's `associatePubKey` with `victimKey`'s pubkey. `helpers.GetAddressesFromPubkeyBytes` derives `victimTrueSei != victimCast` (as shown in `app/setcode_authority_test.go` lines 27-31). `associateAddresses`'s check `GetEVMAddress(ctx, victimTrueSei)` returns not-found, so it proceeds and calls `SetAddressMapping(ctx, victimTrueSei, victimEVM)`.
4. After this call, `GetSeiAddress(ctx, victimEVM) == victimTrueSei`, but `GetEVMAddress(ctx, victimCast)` still returns `victimEVM` — a stale, one-directional leftover mapping that never gets cleaned up.

### Citations

**File:** precompiles/addr/addr.go (L239-244)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
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

**File:** x/evm/keeper/address.go (L24-28)
```go
func (k *Keeper) DeleteAddressMapping(ctx sdk.Context, seiAddress sdk.AccAddress, evmAddress common.Address) {
	store := ctx.KVStore(k.storeKey)
	store.Delete(types.EVMAddressToSeiAddressKey(evmAddress))
	store.Delete(types.SeiAddressToEVMAddressKey(seiAddress))
}
```

**File:** app/ante/cosmos_checktx.go (L530-560)
```go
		if evmAddr, associated := evmKeeper.GetEVMAddress(ctx, signer); associated {
			events = append(events, sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		if acc.GetPubKey() == nil {
			logger.Error("missing pubkey for signer", "signer", signer)
			events = append(events, sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		pk, err := btcec.ParsePubKey(acc.GetPubKey().Bytes())
		if err != nil {
			logger.Debug("failed to parse pubkey, likely due to the fact that it isn't on secp256k1 curve", "pub-key", acc.GetPubKey(), "err", err)
			events = append(events, sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		evmAddr, err := helpers.PubkeyToEVMAddress(pk.SerializeUncompressed())
		if err != nil {
			logger.Error("failed to get EVM address from pubkey", "err", err)
			events = append(events, sdk.NewEvent(evmtypes.EventTypeSigner,
				sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
			continue
		}
		events = append(events, sdk.NewEvent(evmtypes.EventTypeSigner,
			sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
			sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, signer.String())))
		evmKeeper.SetAddressMapping(ctx, signer, evmAddr)
		associationHelper := helpers.NewAssociationHelper(evmKeeper, evmKeeper.BankKeeper(), accountKeeper)
```

**File:** x/evm/ante/preprocess_test.go (L32-36)
```go
// TestPreprocessAssociatesSetCodeAuthorities verifies the EIP-7702 root-cause fix: when a
// sponsored SetCode transaction is preprocessed, each authorization authority is associated
// with its true (pubkey-derived) Sei address before EVM execution. This ensures SetCode
// will not create a mutable direct-cast mapping for the authority that a later
// associatePubKey could remap.
```
