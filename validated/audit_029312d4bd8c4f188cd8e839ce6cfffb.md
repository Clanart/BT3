### Title
Mature unbonding delegations permanently frozen when the delegator becomes unable to receive funds (blocked/unreceivable recipient) - ([File: sei-cosmos/x/staking/keeper/delegation.go])

### Summary
This is the same bug class as the Sherlock `StakedEXA::harvest()` finding: a state-changing operation embedded in a broader lifecycle flow (there, "harvest on deposit"; here, "complete mature unbonding at EndBlock") unconditionally attempts an external transfer/withdrawal without checking the receiver's ability to accept funds first, and the surrounding batch/queue logic swallows the failure in a way that permanently drops the underlying obligation rather than retrying it.

### Finding Description
`CompleteUnbonding` in `sei-cosmos/x/staking/keeper/delegation.go` removes a mature entry from the in-memory `UnbondingDelegation` object and then calls `k.bankKeeper.UndelegateCoinsFromModuleToAccount(ctx, types.NotBondedPoolName, delegatorAddress, ...)`. If that call fails (e.g., because `delegatorAddress` is currently blocked or otherwise fails `CanSendTo`), the function returns the error immediately, before ever calling `k.SetUnbondingDelegation(ctx, ubd)` to persist the entry removal: [1](#0-0) 

This is invoked from `BlockValidatorUpdates` (staking's `EndBlock` routine), which first calls `DequeueAllMatureUBDQueue` — which **unconditionally deletes the time-slice key from the queue regardless of whether the entries are later completed successfully** — and then calls `CompleteUnbonding` for each dequeued pair. If `CompleteUnbonding` returns an error, the loop simply does `continue`, discarding the failure with no retry or fallback path: [2](#0-1) 

Because the timeslice was already deleted from `UBDQueueTimeSlice` before the send was attempted, there is no other code path that re-inserts this entry back into the queue. The underlying `UnbondingDelegation` record persists in the store, but it will never again be considered "mature and due" by the queue-processing logic, so the funds sit in the `NotBondedPool` module account indefinitely with no way for the delegator (or anyone) to trigger a retry, even if the condition that made them unreceivable is later reversed.

This exact scenario — a delegator becoming unable to receive funds (e.g., via re-associating their EVM address to a different Sei address with `SetAddressMapping`, which changes what `CanSendTo`/`BlockedAddr` resolve to for the previously-used address) — is explicitly covered by an existing regression test that confirms the "retained but never re-attempted" behavior: [3](#0-2) 

Notably, the codebase's own commit history shows this exact bug class was identified and mitigated in the **distribution** module (`SetWithdrawAddr` rejects unreceivable withdraw addresses up front; `AfterValidatorRemoved` falls back to routing funds to the community pool instead of panicking/reverting) and the **bank** module's `UndelegateCoins` (which now checks `CanSendTo` atomically before mutating any balances): [4](#0-3) [5](#0-4) 

However, unlike distribution's `AfterValidatorRemoved`, which has a fallback (send to community pool) that conserves value, `CompleteUnbonding`'s failure path has **no fallback**: the coins are not returned anywhere, the entry is not requeued, and it is simply left "retained" forever in a queue-less limbo, matching the confirmatory test assertion (`retained, found := app.StakingKeeper.GetUnbondingDelegation(...); require.True(t, found)` with no further mechanism shown to ever complete it).

### Impact Explanation
The delegator's unbonded principal becomes permanently frozen in the `NotBondedPool` module account: it is never returned to the delegator, and (based on the code reachable from `BlockValidatorUpdates`/`DequeueAllMatureUBDQueue`) there is no other keeper method that re-queues an already-dequeued, still-present `UnbondingDelegation` entry for a later completion attempt. This is a concrete, permanent loss of access to funds for the affected delegator, which meets the "permanent freezing" bar for a valid finding, and is directly analogous in root cause to the referenced Sherlock report: an unconditional external-state-dependent transfer embedded in a batch/lifecycle process, without a receivability check, that DoSes (here, permanently) the completion of a legitimate user flow.

### Likelihood Explanation
The trigger condition — a delegator's designated recipient address becoming unable to receive funds while an unbonding delegation is in flight — is reachable through ordinary, unprivileged user actions on Sei: re-associating an EVM address to a different Sei address (`SetAddressMapping`, exercised via Associate transactions/`associatePubKey`) changes `CanSendTo` resolution for previously-used cast addresses, exactly as demonstrated in the existing distribution/staking regression tests that construct this state via `app.EvmKeeper.SetAddressMapping(...)`. Any user who begins an unbonding period and then re-associates their EVM address (intentionally or not) before the unbonding matures will trigger this path. This makes the likelihood non-trivial and fully user-triggerable without any admin/validator/governance action.

### Recommendation
In `CompleteUnbonding` (and equally for redelegation completions, if similarly affected), do not treat a failed `UndelegateCoinsFromModuleToAccount` as a terminal, unrecoverable failure that leaves the entry permanently un-queued:
- Either persist the entry's removal from the `UnbondingDelegation` object only after the transfer succeeds and, on failure, re-insert the timeslice into `UBDQueueTimeSlice` (e.g., for the next block) so completion is retried automatically once the recipient becomes receivable again; or
- Mirror the distribution module's approach and provide an explicit, safe fallback (e.g., route to a recoverable holding location, or expose a permissionless "retry withdrawal" message that anyone can call once the delegator's address is receivable again) rather than silently discarding the obligation to complete the unbonding.

### Proof of Concept
The existing repository test already demonstrates the vulnerable state transition end-to-end (only lacking a subsequent block to show the entry is never retried, since the queue timeslice has already been consumed): [3](#0-2) 

1. Fund the `NotBondedPool`, associate `delegator`'s address such that `CanSendTo(ctx, delegator)` is `false` (e.g., via `EvmKeeper.SetAddressMapping` re-pointing the underlying EVM address to a different Sei address, exactly as the test does).
2. Create an `UnbondingDelegation` with a mature completion time and `InsertUBDQueue` it.
3. Run `staking.EndBlocker` (which calls `BlockValidatorUpdates` → `DequeueAllMatureUBDQueue` → `CompleteUnbonding`).
4. Observe: no panic, `NotBondedPool` balance unchanged, delegator balance still zero, the `UnbondingDelegation` entry is still `found` in state, but the `UBDQueueIterator` for that maturity time is now empty — confirming the entry will never again be picked up by the queue-driven completion logic, permanently freezing the delegator's principal in the pool.

### Citations

**File:** sei-cosmos/x/staking/keeper/delegation.go (L878-906)
```go
	// loop through all the entries and complete unbonding mature entries
	for i := 0; i < len(ubd.Entries); i++ {
		entry := ubd.Entries[i]
		if entry.IsMature(ctxTime) {
			ubd.RemoveEntry(int64(i))
			i--

			// track undelegation only when remaining or truncated shares are non-zero
			if !entry.Balance.IsZero() {
				amt := sdk.NewCoin(bondDenom, entry.Balance)
				if err := k.bankKeeper.UndelegateCoinsFromModuleToAccount(
					ctx, types.NotBondedPoolName, delegatorAddress, sdk.NewCoins(amt),
				); err != nil {
					return nil, err
				}

				balances = balances.Add(amt)
			}
		}
	}

	// set the unbonding delegation or remove it if there are no more entries
	if len(ubd.Entries) == 0 {
		k.RemoveUnbondingDelegation(ctx, ubd)
	} else {
		k.SetUnbondingDelegation(ctx, ubd)
	}

	return balances, nil
```

**File:** sei-cosmos/x/staking/keeper/val_state_change.go (L35-57)
```go
	// Remove all mature unbonding delegations from the ubd queue.
	matureUnbonds := k.DequeueAllMatureUBDQueue(ctx, ctx.BlockHeader().Time)
	for _, dvPair := range matureUnbonds {
		addr, err := sdk.ValAddressFromBech32(dvPair.ValidatorAddress)
		if err != nil {
			panic(err)
		}
		delegatorAddress := sdk.MustAccAddressFromBech32(dvPair.DelegatorAddress)

		balances, err := k.CompleteUnbonding(ctx, delegatorAddress, addr)
		if err != nil {
			continue
		}

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeCompleteUnbonding,
				sdk.NewAttribute(sdk.AttributeKeyAmount, balances.String()),
				sdk.NewAttribute(types.AttributeKeyValidator, dvPair.ValidatorAddress),
				sdk.NewAttribute(types.AttributeKeyDelegator, dvPair.DelegatorAddress),
			),
		)
	}
```

**File:** sei-cosmos/x/staking/keeper/delegation_test.go (L281-326)
```go
func TestEndBlockRetainsUnbondingForInvalidRecipient(t *testing.T) {
	_, app, ctx := createTestInput(t)
	maturity := time.Unix(1, 0).UTC()
	ctx = ctx.WithBlockTime(maturity)
	evmAddr := common.HexToAddress("0x3333333333333333333333333333333333333333")
	delegator := sdk.AccAddress(evmAddr[:])
	app.EvmKeeper.SetAddressMapping(ctx, delegator, evmAddr)
	app.EvmKeeper.SetAddressMapping(
		ctx,
		sdk.AccAddress(common.HexToAddress("0x4444444444444444444444444444444444444444").Bytes()),
		evmAddr,
	)
	validator := sdk.ValAddress(seiapp.AddTestAddrsIncremental(app, ctx, 1, sdk.NewInt(10000))[0])
	amount := sdk.NewInt(40)
	bondDenom := app.StakingKeeper.BondDenom(ctx)
	notBondedPool := app.StakingKeeper.GetNotBondedPool(ctx)
	coins := sdk.NewCoins(sdk.NewCoin(bondDenom, amount))

	require.NoError(t, apptesting.FundModuleAccount(
		app.BankKeeper,
		ctx,
		notBondedPool.GetName(),
		coins,
	))

	unbonding := types.NewUnbondingDelegation(delegator, validator, 0, maturity, amount)
	app.StakingKeeper.SetUnbondingDelegation(ctx, unbonding)
	app.StakingKeeper.InsertUBDQueue(ctx, unbonding, maturity)
	require.False(t, app.BankKeeper.CanSendTo(ctx, delegator))

	poolBalanceBefore := app.BankKeeper.GetBalance(ctx, notBondedPool.GetAddress(), bondDenom)
	require.NotPanics(t, func() {
		staking.EndBlocker(ctx, app.StakingKeeper)
	})

	poolBalanceAfter := app.BankKeeper.GetBalance(ctx, notBondedPool.GetAddress(), bondDenom)
	require.Equal(t, poolBalanceBefore, poolBalanceAfter)
	require.True(t, app.BankKeeper.GetBalance(ctx, delegator, bondDenom).IsZero())
	retained, found := app.StakingKeeper.GetUnbondingDelegation(ctx, delegator, validator)
	require.True(t, found)
	require.Equal(t, unbonding, retained)

	iterator := app.StakingKeeper.UBDQueueIterator(ctx, maturity)
	require.False(t, iterator.Valid())
	require.NoError(t, iterator.Close())
}
```

**File:** sei-cosmos/x/distribution/keeper/hooks.go (L44-72)
```go
		// add to validator account
		if !coins.IsZero() {
			accAddr := sdk.AccAddress(valAddr)
			withdrawAddr := h.k.GetDelegatorWithdrawAddr(ctx, accAddr)

			// GetDelegatorWithdrawAddr falls back to the delegator (accAddr) when the
			// configured withdraw address cannot receive funds, but that fallback can
			// itself be unable to receive — e.g. accAddr is an EVM address whose Sei
			// mapping was re-associated to a different address, so CanAddressReceive
			// rejects it. This hook runs in EndBlock, so attempting the send and
			// panicking on the resulting bank error would halt the chain. Check
			// receivability first: when the recipient cannot receive, route the
			// commission to the community pool instead. The coins already back the
			// distribution module account (where community pool funds are held), so this
			// conserves value and avoids the partial module-account debit that a failed
			// SendCoins leaves behind.
			if h.k.canReceiveWithdrawAddr(ctx, withdrawAddr) {
				if err := h.k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, withdrawAddr, coins); err != nil {
					panic(err)
				}
			} else {
				feePool := h.k.GetFeePool(ctx)
				decCoins, err := sdk.NewDecCoinsFromCoins(coins...)
				if err != nil {
					panic(err)
				}
				feePool.CommunityPool = feePool.CommunityPool.Add(decCoins...)
				h.k.SetFeePool(ctx, feePool)
			}
```

**File:** sei-cosmos/x/bank/keeper/keeper.go (L259-292)
```go
// UndelegateCoins performs undelegation by crediting amt coins to an account with
// address addr. For vesting accounts, undelegation amounts are tracked for both
// vesting and vested coins. The coins are then transferred from a ModuleAccount
// address to the delegator address. If any of the undelegation amounts are
// negative, an error is returned.
func (k BaseKeeper) UndelegateCoins(ctx sdk.Context, moduleAccAddr, delegatorAddr sdk.AccAddress, amt sdk.Coins) error {
	moduleAcc := k.ak.GetAccount(ctx, moduleAccAddr)
	if moduleAcc == nil {
		return sdkerrors.Wrapf(sdkerrors.ErrUnknownAddress, "module account %s does not exist", moduleAccAddr)
	}

	if !amt.IsValid() {
		return sdkerrors.Wrap(sdkerrors.ErrInvalidCoins, amt.String())
	}
	if !k.CanSendTo(ctx, delegatorAddr) {
		return sdkerrors.ErrInvalidRecipient
	}

	err := k.SubUnlockedCoins(ctx, moduleAccAddr, amt, true)
	if err != nil {
		return err
	}

	if err := k.trackUndelegation(ctx, delegatorAddr, amt); err != nil {
		return sdkerrors.Wrap(err, "failed to track undelegation")
	}

	err = k.AddCoins(ctx, delegatorAddr, amt, true)
	if err != nil {
		return err
	}

	return nil
}
```
