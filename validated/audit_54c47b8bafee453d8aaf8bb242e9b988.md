### Title
Delegator reward withdrawals permanently fail once a direct-cast delegator address is re-associated to a different Sei address - (File: sei-cosmos/x/distribution/keeper/store.go)

### Summary
A delegator that staked (or received an authorized delegation) under the "direct-cast" Sei address of an EVM address — i.e. before that EVM address was ever truly associated — can permanently lose the ability to withdraw already-accrued staking rewards once the EVM address is later re-associated (e.g. via `associatePubKey`) to a different, true Sei address. This mirrors the Truflation `migrateUser`/`VirtualStakingRewards` bug class: value that accrued under an identity is stranded once that identity is remapped, because the reward-payout path still targets the old (now unreceivable) address.

### Finding Description
Sei-chain lets an EVM address be used before it has a "true" pubkey-derived Sei-address association: any operation defaults to the direct-cast address, `sdk.AccAddress(evmAddr[:])`, via `GetSeiAddressOrDefault`/`GetEVMAddressOrDefault` [1](#0-0) . A delegation (direct or via `delegateWithAuthorization`) can be created with that cast address as `DelegatorAddress` [2](#0-1) .

Once the true owner later calls `associatePubKey`/`associate` (or an EIP-7702 SetCode installs a delegated identity), `SetAddressMapping` overwrites the EVM→Sei mapping to point at a different, true Sei address [3](#0-2) . After this remap, `CanAddressReceive` explicitly returns `false` for the old cast address, because the associated Sei address for that EVM address is no longer the cast address itself [4](#0-3) .

The distribution module's reward-withdraw path pays rewards to `GetDelegatorWithdrawAddr`, which defaults to the delegator address itself when no separate withdraw address was set, and only falls back away from a withdraw address if it is unreceivable — but the delegator address itself is used as the *fallback*, so if the delegator address is the one that has become unreceivable, `GetDelegatorWithdrawAddr` still returns the (now-blocked) delegator/cast address [5](#0-4) . `withdrawDelegationRewards` then calls `SendCoinsFromModuleToAccount` to that same unreceivable address [6](#0-5) , which fails.

This exact bug class (rewards/state orphaned under a direct-cast identity after re-association) is explicitly acknowledged in the codebase's own comments and is only mitigated for the *validator-commission* / `AfterValidatorRemoved` EndBlock path, which was hardened to route funds to the community pool instead of panicking [7](#0-6) . The delegate/EIP-7702 comment confirms the general risk: "orphaning any staking/distribution state created under the direct-cast identity" [8](#0-7) . However, no equivalent mitigation exists for a plain delegator's own pending/future reward withdrawals when the *delegator* address itself (not the validator operator) becomes unreceivable after re-association — the transaction simply errors out on every subsequent withdrawal attempt, and the module retains the coins in `ValidatorOutstandingRewards`/module balance with no route for the rightful staker to claim them.

### Impact Explanation
The staker's already-accrued and any future-accruing rewards for that validator become permanently unclaimable through the normal `WithdrawDelegationRewards`/precompile path, because every attempt targets the same unreceivable address and fails. This is a permanent freezing of funds legitimately owed to the user (functionally identical in outcome to the Truflation report: rewards stranded at an address the user can no longer use). Unlike the validator-commission case, there is no fallback that redirects the payment or lets the user redirect it (they cannot call `SetWithdrawAddr` from the delegator address anymore either, since the underlying EVM key now maps to a different Sei address and cannot sign `MsgSetWithdrawAddress` as the old delegator).

### Likelihood Explanation
Reachable by an ordinary, unprivileged user with only EVM transactions and the `staking`/`addr` precompiles: (1) delegate via the staking precompile from an EVM address before ever performing a true association (or receive an authorized delegation via `delegateWithAuthorization` under a not-yet-associated grantor), letting rewards accrue against the cast delegator address, then (2) call `associatePubKey`/`associate` to bind that EVM address to a different true Sei address. No governance, validator, or peer-level access is required — this is entirely self-inflicted or triggerable by directing another user's cast-address delegation and later helping/inducing them to associate.

### Recommendation
When `SetAddressMapping` remaps an EVM address away from its own direct-cast Sei address, proactively force-withdraw any outstanding delegation rewards for that cast address into a receivable destination (e.g., the newly associated true address), mirroring the fix already applied to `AfterValidatorRemoved` for commissions. Alternatively, extend `GetDelegatorWithdrawAddr`'s unreceivable-fallback logic so that when the *delegator* address itself is unreceivable, the withdraw resolves through the EVM/Sei association mapping to the newly associated true address rather than looping back to the same blocked address.

### Proof of Concept
1. From an unassociated EVM address `E`, call `delegate(validator)` on the staking precompile with `value > 0`; `GetSeiAddress` is not yet set for `E`'s owner (or use `delegateWithAuthorization` for a grantor whose EVM address is not yet truly associated), so the delegation is recorded under `castAddr = sdk.AccAddress(E[:])` [9](#0-8) .
2. Let rewards accrue for several blocks.
3. Call `associate`/`associatePubKey` for `E`, causing `SetAddressMapping(ctx, trueSeiAddr, E)` to overwrite the mapping so `GetSeiAddress(E) == trueSeiAddr != castAddr` [3](#0-2) .
4. Now `CanAddressReceive(ctx, castAddr)` returns `false` [10](#0-9) .
5. Call `withdrawDelegationRewards(validator)` (or a native `MsgWithdrawDelegatorReward` with `DelegatorAddress = castAddr`, if it can still be signed): `GetDelegatorWithdrawAddr` returns `castAddr` (no override was set) [5](#0-4) , and `SendCoinsFromModuleToAccount(ctx, distrModule, castAddr, rewards)` fails because `castAddr` is unreceivable, per the bank keeper's `CanSendTo`/`BlockedAddr` gating referenced by `canReceiveWithdrawAddr` [11](#0-10) . The withdrawal errors on every retry — the rewards are stuck.

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

**File:** x/evm/keeper/address.go (L41-64)
```go
func (k *Keeper) GetEVMAddressOrDefault(ctx sdk.Context, seiAddress sdk.AccAddress) common.Address {
	addr, ok := k.GetEVMAddress(ctx, seiAddress)
	if ok {
		return addr
	}
	return common.BytesToAddress(seiAddress)
}

func (k *Keeper) GetSeiAddress(ctx sdk.Context, evmAddress common.Address) (sdk.AccAddress, bool) {
	store := ctx.KVStore(k.storeKey)
	bz := store.Get(types.EVMAddressToSeiAddressKey(evmAddress))
	if bz == nil {
		return []byte{}, false
	}
	return bz, true
}

func (k *Keeper) GetSeiAddressOrDefault(ctx sdk.Context, evmAddress common.Address) sdk.AccAddress {
	addr, ok := k.GetSeiAddress(ctx, evmAddress)
	if ok {
		return addr
	}
	return sdk.AccAddress(evmAddress[:])
}
```

**File:** x/evm/keeper/address.go (L78-86)
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

**File:** precompiles/staking/staking.go (L392-431)
```go
func (p PrecompileExecutor) delegateWithAuthorization(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, hooks *tracing.Hooks, evm *vm.EVM) ([]byte, uint64, error) {
	if err := pcommon.ValidateArgsLength(args, 2); err != nil {
		return nil, 0, err
	}
	grantee, err := pcommon.GetSeiAddressByEvmAddress(ctx, caller, p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}
	delegator, err := pcommon.GetSeiAddressFromArg(ctx, args[0], p.evmKeeper)
	if err != nil {
		return nil, 0, err
	}
	return p.delegateFor(ctx, method, delegator, args[0].(common.Address), args[1].(string), value, hooks, evm, p.authorizedStakingExecutor(ctx, grantee))
}

// delegateFor performs the shared direct and authorized delegation steps. The
// delegator must be explicitly associated because a later association cannot
// safely merge a delegation created under its cast address.
func (p PrecompileExecutor) delegateFor(ctx sdk.Context, method *abi.Method, delegator sdk.AccAddress, delegatorEVM common.Address, validatorBech32 string, value *big.Int, hooks *tracing.Hooks, evm *vm.EVM, execute stakingMessageExecutor) ([]byte, uint64, error) {
	if value == nil || value.Sign() == 0 {
		return nil, 0, errors.New("set `value` field to non-zero to send delegate fund")
	}
	coin, err := pcommon.HandlePaymentUsei(ctx, p.evmKeeper.GetSeiAddressOrDefault(ctx, p.address), delegator, value, p.bankKeeper, p.evmKeeper, hooks, evm.GetDepth())
	if err != nil {
		return nil, 0, err
	}

	withdrawAddress := p.distributionKeeper.GetDelegatorWithdrawAddr(ctx, delegator)
	withdrawAddressBalanceBefore := p.bankKeeper.GetBalance(ctx, withdrawAddress, sdk.MustGetBaseDenom())
	msg := &stakingtypes.MsgDelegate{
		DelegatorAddress: delegator.String(),
		ValidatorAddress: validatorBech32,
		Amount:           coin,
	}
	if err := msg.ValidateBasic(); err != nil {
		return nil, 0, err
	}
	if err := execute(msg); err != nil {
		return nil, 0, err
	}
```

**File:** sei-cosmos/x/distribution/keeper/store.go (L10-22)
```go
// get the delegator withdraw address, defaulting to the delegator address
func (k Keeper) GetDelegatorWithdrawAddr(ctx sdk.Context, delAddr sdk.AccAddress) sdk.AccAddress {
	store := ctx.KVStore(k.storeKey)
	b := store.Get(types.GetDelegatorWithdrawAddrKey(delAddr))
	if b == nil {
		return delAddr
	}
	withdrawAddr := sdk.AccAddress(b)
	if !k.canReceiveWithdrawAddr(ctx, withdrawAddr) {
		return delAddr
	}
	return withdrawAddr
}
```

**File:** sei-cosmos/x/distribution/keeper/delegation.go (L286-293)
```go
	// add coins to user account
	if !finalRewards.IsZero() {
		withdrawAddr := k.GetDelegatorWithdrawAddr(ctx, del.GetDelegatorAddr())
		err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, withdrawAddr, finalRewards)
		if err != nil {
			return nil, err
		}
	}
```

**File:** sei-cosmos/x/distribution/keeper/keeper_test.go (L95-103)
```go
// TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator covers the
// case where the validator operator address itself cannot receive funds — its EVM
// address was re-associated (e.g. via associatePubKey) away from the direct-cast Sei
// address it was created under. The withdraw-address fallback in GetDelegatorWithdrawAddr
// resolves back to that same unreceivable operator address, so the commission
// force-withdraw fails. AfterValidatorRemoved runs during EndBlock, so it must not panic:
// the commission is routed to the community pool instead, which conserves value because
// the coins already back the distribution module account.
func TestAfterValidatorRemovedRoutesToCommunityPoolForUnreceivableValidator(t *testing.T) {
```

**File:** app/ante/evm_checktx.go (L251-258)
```go
// AssociateAuthorizationAuthorities pre-associates every EIP-7702 SetCode authorization
// authority in the transaction with its true (pubkey-derived) Sei address before EVM
// execution installs delegation code for it. Authorities are distinct accounts from the tx
// sender, so the sender association performed by the caller does not cover them. Without
// this, SetCode creates a mutable direct-cast EVM->Sei mapping that a later associatePubKey
// call can remap, orphaning any staking/distribution state created under the direct-cast
// identity (which can then halt the chain via the distribution validator-removal hook).
//
```

**File:** sei-cosmos/x/distribution/keeper/keeper.go (L86-96)
```go
func (k Keeper) canReceiveWithdrawAddr(ctx sdk.Context, withdrawAddr sdk.AccAddress) bool {
	// BlockedAddr mirrors the gate SendCoinsFromModuleToAccount actually enforces:
	// beyond the module-account blocklist it also rejects dynamically-derived
	// addresses such as the EVM coinbase addresses (an "evm_coinbase"-prefixed
	// address), which CanSendTo does not catch. Consulting it here keeps this
	// predicate in lockstep with the send, so AfterValidatorRemoved never concludes
	// an address is receivable and then panics on the resulting bank error.
	return !k.blockedAddrs[withdrawAddr.String()] &&
		!k.bankKeeper.BlockedAddr(withdrawAddr) &&
		k.bankKeeper.CanSendTo(ctx, withdrawAddr)
}
```
