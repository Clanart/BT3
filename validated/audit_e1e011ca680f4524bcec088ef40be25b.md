### Title
Address (re-)association overwrites existing bidirectional EVM↔Sei mapping without dropping the stale reverse entry - ([File: x/evm/keeper/address.go])

### Summary
`SetAddressMapping` unconditionally writes both halves of the EVM↔Sei bidirectional address-mapping index without first checking for or clearing any pre-existing mapping that used the same key on the "other side" of the bijection. The `addr` precompile's `associate`/`associatePubKey` handlers only guard against the new Sei address already being associated (`GetEVMAddress(ctx, seiAddr)`), but never check whether the derived EVM address is already associated with a *different* Sei address (`GetSeiAddress(ctx, evmAddr)`) before calling `SetAddressMapping`. This is the same bug class as the KVM report: a new mapping is installed over an existing "present" entry without first dropping/zapping the stale side, leaving an inconsistent bidirectional index.

### Finding Description
`SetAddressMapping` writes both directions of the mapping unconditionally: [1](#0-0) 

The precompile-level guard in `associateAddresses` (and all legacy versions) only checks that the *new* Sei address isn't already associated — it never checks whether `evmAddr` is already the target of a different Sei address's mapping: [2](#0-1) 

The same one-sided check exists in every legacy precompile revision (v575, v600, v601, v605, v606, v610, v614, v620, v630, v66, v67), so the gap has persisted across the address-association precompile's entire history: [3](#0-2) 

`AssociateAddresses` in the helper layer is what actually calls `SetAddressMapping`, again without any check that `evmAddr` is unclaimed: [4](#0-3) 

If a Sei address `A` is already associated with EVM address `X` (i.e. `SeiAddressToEVMAddressKey(A) -> X` and `EVMAddressToSeiAddressKey(X) -> A` both exist), and later a different Sei address `B` (never previously associated) presents a valid signature/pubkey that resolves to the *same* EVM address `X` (e.g. `associatePubKey`, where the EVM address is derived purely from the pubkey and is independent of any invariant tying it to a single Sei account), `SetAddressMapping(ctx, B, X)` will:
- overwrite `EVMAddressToSeiAddressKey(X)` from `A` to `B`,
- write `SeiAddressToEVMAddressKey(B) -> X`,
- but **never delete** the old `SeiAddressToEVMAddressKey(A) -> X` entry.

The store is left with `A -> X` and `X -> B` simultaneously: the forward and reverse indexes no longer agree, exactly analogous to the KVM bug where a new (MMIO) mapping is installed while the stale "present" entry for the old mapping is never dropped/zapped.

### Impact Explanation
The bidirectional map is relied upon by fund-routing and gating logic. `CanAddressReceive` uses the reverse mapping to decide whether a bank transfer to a cast address (`sdk.AccAddress(evmAddress[:])`) should be blocked once that EVM address has a "true" association: [5](#0-4) 

`GetEVMAddress`/`GetEVMAddressOrDefault` (used pervasively by the StateDB usei/wei bridge, fee logic, and EVM message routing) also depend on this index being one-to-one and consistent: [6](#0-5) 

Once the reverse entry for `A` becomes stale (still pointing at `X`, which now belongs to `B`), `GetEVMAddressOrDefault(ctx, A)` continues to resolve `A`'s EVM identity to `X`, while `GetSeiAddress(ctx, X)` now resolves to `B`. Any code path that looks up one address and derives balances/permissions from the other (e.g. `CanAddressReceive`, EVM tx sender resolution, `MigrateBalance`) can now disagree about which Sei account "owns" `X`, which is a state-consistency corruption directly reachable by an unprivileged EVM/precompile caller and can be leveraged for fund misdirection or association-based access-control bypass.

### Likelihood Explanation
Reachable by any account: the `addr` precompile's `associate`/`associatePubKey` functions are public, unprivileged transaction entry points, callable by any EVM sender who can produce a signature/pubkey. The only requirement is presenting a pubkey (or signature) that resolves to an EVM address `X` that is already associated with someone else's Sei address `A` — a scenario plausible whenever address association happens more than once for overlapping addresses (e.g., re-association after key rotation, a previously auto-associated cast address, or any other code path that calls `SetAddressMapping` for the same EVM address under a different Sei address). No special privileges or a specific chain configuration are needed beyond the reachable public precompile call.

### Recommendation
Before calling `SetAddressMapping`, both directions must be checked (not just the Sei-address side), and any existing stale mapping must be dropped/zapped first:
- In `associateAddresses` (and all legacy variants), also call `GetSeiAddress(ctx, evmAddr)`; if it returns an existing, different Sei address, reject the association (or explicitly call `DeleteAddressMapping` for the old pair first).
- Alternatively, harden `SetAddressMapping` itself to detect and clear any pre-existing reverse/forward entries before writing the new bijection, mirroring the kernel fix's pattern of "zap the existing present entry before installing the new one."

### Proof of Concept
1. Attacker/Party A associates Sei address `A` with EVM address `X` via `associatePubKey` (or `associate`), establishing `SeiAddressToEVMAddressKey(A) -> X` and `EVMAddressToSeiAddressKey(X) -> A`.
2. A different Sei address `B` (unassociated) submits `associatePubKey`/`associate` with a pubkey/signature that resolves to the same EVM address `X` (e.g., `B` controls a pubkey mapping to `X` through the deterministic derivation in `helpers.GetAddresses`/`GetAddressesFromPubkeyBytes`).
3. `associateAddresses` only checks `GetEVMAddress(ctx, B)` (not found, since `B` was never associated) and proceeds to call `AssociateAddresses` → `SetAddressMapping(ctx, B, X)`.
4. Post-call state: `SeiAddressToEVMAddressKey(A) -> X` (stale, unmodified) and `EVMAddressToSeiAddressKey(X) -> B` (new) coexist, while `SeiAddressToEVMAddressKey(B) -> X` is also newly written — the mapping is no longer one-to-one and `GetSeiAddress`/`GetEVMAddress`/`CanAddressReceive` now disagree about the ownership of `X`, corrupting downstream fund-routing decisions such as `CanAddressReceive` and `MigrateBalance`.

### Citations

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

**File:** precompiles/addr/addr.go (L239-255)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(seiAddr.String(), evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** precompiles/addr/legacy/v601/addr.go (L233-245)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}
```

**File:** utils/helpers/associate.go (L34-55)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}
```

**File:** giga/deps/xevm/keeper/address.go (L30-47)
```go
func (k *Keeper) GetEVMAddress(ctx sdk.Context, seiAddress sdk.AccAddress) (common.Address, bool) {
	store := k.GetKVStore(ctx)
	bz := store.Get(types.SeiAddressToEVMAddressKey(seiAddress))
	addr := common.Address{}
	if bz == nil {
		return addr, false
	}
	copy(addr[:], bz)
	return addr, true
}

func (k *Keeper) GetEVMAddressOrDefault(ctx sdk.Context, seiAddress sdk.AccAddress) common.Address {
	addr, ok := k.GetEVMAddress(ctx, seiAddress)
	if ok {
		return addr
	}
	return common.BytesToAddress(seiAddress)
}
```

**File:** giga/deps/xevm/keeper/address.go (L78-86)
```go
// A sdk.AccAddress may not receive funds from bank if it's the result of direct-casting
// from an EVM address AND the originating EVM address has already been associated with
// a true (i.e. derived from the same pubkey) sdk.AccAddress.
func (k *Keeper) CanAddressReceive(ctx sdk.Context, addr sdk.AccAddress) bool {
	directCast := common.BytesToAddress(addr) // casting goes both directions since both address formats have 20 bytes
	associatedAddr, isAssociated := k.GetSeiAddress(ctx, directCast)
	// if the associated address is the cast address itself, allow the address to receive (e.g. EVM contract addresses)
	return associatedAddr.Equals(addr) || !isAssociated // this means it's either a cast address that's not associated yet, or not a cast address at all.
}
```
