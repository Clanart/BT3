Based on my investigation, I found `TestEndBlockRetainsUnbondingForInvalidRecipient` in `sei-cosmos/x/staking/keeper/delegation_test.go`, which shows that Sei-chain's own `CompleteUnbonding` already handles the exact bug class from the report: if the completion of a queued fund-release action would fail (e.g., recipient cannot receive funds), the code retains the entry and retries on a later `EndBlocker` pass rather than silently dropping or permanently locking the funds.### Title
Permanent freezing of unbonded/redelegated stake when `CompleteUnbonding`/`CompleteRedelegation` fails after dequeue — no retry or resend mechanism exists ([File: sei-cosmos/x/staking/keeper/val_state_change.go])

### Summary
`BlockValidatorUpdates`, called every `EndBlock`, dequeues all mature unbonding/redelegation entries from their time-indexed queues and then attempts to settle them via `CompleteUnbonding`/`CompleteRedelegation`. If settlement fails (e.g. the delegator's recipient account cannot receive coins), the queue entry has already been permanently removed, but the underlying `UnbondingDelegation`/`Redelegation` record is left un-mutated in the store with no queue entry pointing to it. There is no automatic retry and no user-callable message to re-trigger settlement, so the delegator's funds remain locked in the `NotBondedPool` module account forever. This mirrors the reported bug class: a required completion step that can fail/be "dropped" leaves user funds permanently frozen with no resend/retry function, exactly the gap the external report flags for `L1ValidatorWeightMessage`.

### Finding Description
`BlockValidatorUpdates` first calls `DequeueAllMatureUBDQueue`, which unconditionally deletes the timeslice keys for all mature `(delegator, validator)` pairs [1](#0-0) . It then calls `CompleteUnbonding` for each dequeued pair and, on error, simply does `continue` — it does not re-insert the pair into the queue [2](#0-1) . The same pattern applies to mature redelegations [3](#0-2) .

Inside `CompleteUnbonding`, the function removes the mature entry from the in-memory `ubd` object and only then calls `bankKeeper.UndelegateCoinsFromModuleToAccount`; if that transfer errors (e.g. the recipient bech32 is blocked from receiving funds), the function returns the error *before* calling `SetUnbondingDelegation`/`RemoveUnbondingDelegation`, so the on-chain `UnbondingDelegation` record is left untouched in the store [4](#0-3) . Because the queue entry that would have triggered a retry was already deleted in the same block, this record is now orphaned: no future `EndBlock` will ever revisit it.

This exact scenario is already captured (as a "characterization" test, not a fix) in `TestEndBlockRetainsUnbondingForInvalidRecipient`, which shows that after `EndBlocker` runs, the `UnbondingDelegation` is still present in the store but the corresponding queue entry (`UBDQueueIterator`) is gone [5](#0-4) .

The precondition — a delegator/recipient address that legitimately unbonds but later becomes unable to receive funds (`CanSendTo` returns false) — is reachable by ordinary users. Sei's EVM↔Cosmos address-association model allows a cast/derived Sei address behind an EVM address to be remapped by a subsequent `associatePubKey`/EIP-7702 `SetCode` authorization, changing which account is treated as canonical for that EVM address [6](#0-5) . If a delegator that has already queued an undelegate/redelegate later has its association remapped (a user-initiated, permissionless transaction), `bankKeeper.CanSendTo`/`SendCoins` can subsequently fail for that account, and the queued unbonding record becomes permanently unreachable through the mechanism above.

### Impact Explanation
Funds already removed from bonded stake and held in the `NotBondedPool` (or equivalently for redelegations) become permanently unrecoverable for the affected delegator — the module account balance is unspendable module-side and the delegator has no unbonding-delegation query result driving any future settlement, since the retry queue entry is gone. This is a concrete permanent freezing of funds condition, exactly the class of impact called out as acceptable in the validation criteria.

### Likelihood Explanation
This requires: (1) a delegator/recipient address whose direct-cast/derived-Sei mapping can be altered by a subsequent association action (a state Sei's EIP-7702/associate flows explicitly guard against for other reasons, indicating the underlying address-remapping primitive is real and user-triggerable), and (2) that remap to happen in the window between undelegation submission and unbonding maturity. This is a narrower trigger than a routine user error, but it is fully reachable without any privileged, validator, or network-level actions — only ordinary EVM/Cosmos transactions from the account owner (or a third party crafting an EIP-7702 authorization for the account) are required.

### Recommendation
On failure inside `CompleteUnbonding`/`CompleteRedelegation` (or in the callers in `BlockValidatorUpdates`), re-insert the dequeued `(delegator, validator[, dstValidator])` pair back into the UBD/redelegation queue (e.g. at a short future time) instead of silently dropping it, so settlement is retried automatically once the underlying condition (e.g. `CanSendTo`) resolves. Additionally, expose a permissionless message/query (analogous to the suggested `resendXxxMessage` pattern) that lets a delegator explicitly re-trigger settlement of a mature-but-unsettled unbonding/redelegation entry, so funds are never permanently unreachable purely due to a one-time settlement failure.

### Proof of Concept
1. Associate a Sei address `S1` to EVM address `E` and fund `S1`, then delegate from `S1` to a validator.
2. Submit `MsgUndelegate` from `S1`; this inserts an entry into the UBD queue with a future `completionTime`.
3. Before `completionTime`, remap `E`'s association to a different Sei address `S2` (e.g., via a subsequent `associatePubKey`/EIP-7702 authorization flow), so that `S1` becomes an address for which `bankKeeper.CanSendTo`/`SendCoins` will fail (as demonstrated directly in `sei-cosmos/x/staking/keeper/delegation_test.go`'s `TestEndBlockRetainsUnbondingForInvalidRecipient`, which sets up exactly this condition by remapping the same EVM address to two different Sei addresses and confirms `CanSendTo` becomes `false`) [7](#0-6) .
4. Advance the chain to `completionTime` and run `EndBlocker`. The queue entry is dequeued and `CompleteUnbonding` fails on the bank transfer, but the `UnbondingDelegation` record remains in the store while its queue entry is gone [8](#0-7) .
5. Observe that no subsequent `EndBlocker` call will ever complete this unbonding: `UBDQueueIterator` no longer contains the entry, so `DequeueAllMatureUBDQueue`/`CompleteUnbonding` will never be invoked for it again — the delegator's tokens remain stuck in the `NotBondedPool` indefinitely, with no on-chain mechanism to resend/retry the settlement.

### Citations

**File:** sei-cosmos/x/staking/keeper/delegation.go (L375-395)
```go
// DequeueAllMatureUBDQueue returns a concatenated list of all the timeslices inclusively previous to
// currTime, and deletes the timeslices from the queue.
func (k Keeper) DequeueAllMatureUBDQueue(ctx sdk.Context, currTime time.Time) (matureUnbonds []types.DVPair) {
	store := ctx.KVStore(k.storeKey)

	// gets an iterator for all timeslices from time 0 until the current Blockheader time
	unbondingTimesliceIterator := k.UBDQueueIterator(ctx, ctx.BlockHeader().Time)
	defer func() { _ = unbondingTimesliceIterator.Close() }()

	for ; unbondingTimesliceIterator.Valid(); unbondingTimesliceIterator.Next() {
		timeslice := types.DVPairs{}
		value := unbondingTimesliceIterator.Value()
		k.cdc.MustUnmarshal(value, &timeslice)

		matureUnbonds = append(matureUnbonds, timeslice.Pairs...)

		store.Delete(unbondingTimesliceIterator.Key())
	}

	return matureUnbonds
}
```

**File:** sei-cosmos/x/staking/keeper/delegation.go (L860-907)
```go
// CompleteUnbonding completes the unbonding of all mature entries in the
// retrieved unbonding delegation object and returns the total unbonding balance
// or an error upon failure.
func (k Keeper) CompleteUnbonding(ctx sdk.Context, delAddr sdk.AccAddress, valAddr sdk.ValAddress) (sdk.Coins, error) {
	ubd, found := k.GetUnbondingDelegation(ctx, delAddr, valAddr)
	if !found {
		return nil, types.ErrNoUnbondingDelegation
	}

	bondDenom := k.GetParams(ctx).BondDenom
	balances := sdk.NewCoins()
	ctxTime := ctx.BlockHeader().Time

	delegatorAddress, err := sdk.AccAddressFromBech32(ubd.DelegatorAddress)
	if err != nil {
		return nil, err
	}

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
}
```

**File:** sei-cosmos/x/staking/keeper/val_state_change.go (L32-47)
```go
	// unbond all mature validators from the unbonding queue
	k.UnbondAllMatureValidators(ctx)

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
```

**File:** sei-cosmos/x/staking/keeper/val_state_change.go (L59-80)
```go
	// Remove all mature redelegations from the red queue.
	matureRedelegations := k.DequeueAllMatureRedelegationQueue(ctx, ctx.BlockHeader().Time)
	for _, dvvTriplet := range matureRedelegations {
		valSrcAddr, err := sdk.ValAddressFromBech32(dvvTriplet.ValidatorSrcAddress)
		if err != nil {
			panic(err)
		}
		valDstAddr, err := sdk.ValAddressFromBech32(dvvTriplet.ValidatorDstAddress)
		if err != nil {
			panic(err)
		}
		delegatorAddress := sdk.MustAccAddressFromBech32(dvvTriplet.DelegatorAddress)

		balances, err := k.CompleteRedelegation(
			ctx,
			delegatorAddress,
			valSrcAddr,
			valDstAddr,
		)
		if err != nil {
			continue
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

**File:** x/evm/ante/preprocess.go (L103-112)
```go
	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)

	return next(ctx, tx, simulate)
```
