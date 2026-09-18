### Title
Unbounded per-block draining of staking unbonding/redelegation queues allows cheap, unmetered griefing of block production - ([File: sei-cosmos/x/staking/keeper/val_state_change.go])

### Summary
The report describes a queue-draining bridge (`batchRelease()`) that has no minimum-deposit and no per-batch skip mechanism, letting an attacker flood the queue with cheap entries that force everyone else to pay for processing them. The Sei staking module has an analogous, and in some ways worse, pattern: `BlockValidatorUpdates`, called unconditionally from every `EndBlocker`, fully drains **all** mature entries in the unbonding-delegation queue and the redelegation queue with no per-block cap, no minimum undelegation amount, and no way to skip/limit entries maturing in the same time bucket.

### Finding Description
`BlockValidatorUpdates` calls `k.DequeueAllMatureUBDQueue(ctx, ctx.BlockHeader().Time)` and `k.DequeueAllMatureRedelegationQueue(ctx, ctx.BlockHeader().Time)`, then iterates over **every** returned entry synchronously, calling `k.CompleteUnbonding` (and the redelegation equivalent) for each one, with no bound on how many entries are processed in a single block: [1](#0-0) 

`DequeueAllMatureUBDQueue` itself unconditionally returns and deletes every timeslice up to the current block time, with no batching: [2](#0-1) 

Unbonding-delegation completion times are computed as `ctx.BlockHeader().Time.Add(k.UnbondingTime(ctx))`, and all entries inserted via `InsertUBDQueue` in the same block land in the same timeslice bucket: [3](#0-2) [4](#0-3) 

There is no minimum undelegation/redelegation amount enforced at the message-handling layer, and the only per-entry cap (`HasMaxUnbondingDelegationEntries` / `MaxEntries`) is scoped to a single (delegator, validator) pair, not to the total number of entries maturing in the same time bucket across all delegator/validator pairs: [5](#0-4) [6](#0-5) 

This is directly analogous to the escrow-bridge bug: cheap entries (small delegations/undelegations, reachable from ordinary `MsgUndelegate`/`MsgBeginRedelegate` transactions, or via the staking precompile from EVM) are appended to a queue keyed by maturity time, and the entire matured bucket is drained unconditionally by a shared, unbatched EndBlocker routine — exactly the "no deposits can be skipped, no per-batch limit, no minimum amount" pattern flagged in the report. Notably, Sei's own governance module (`x/gov/abci.go`) was specifically hardened against this bug class with a `MaxVotesProcessedPerBlock` budget and incremental, resumable processing: [7](#0-6) 
No equivalent per-block budget exists for the staking UBD/redelegation queues.

### Impact Explanation
Unlike the escrow example (where the attacker at least burns gas equal to `bridgeOut()` cost for every queue entry they create), staking queue drainage happens entirely inside `EndBlocker`, which is **not** metered against any single transaction's gas limit. An attacker who creates a very large number of tiny delegations across many delegator addresses/validators and then undelegates (or redelegates) them all within the same block causes all those completions to mature in the same time-slice bucket. When that bucket matures, every validator node must synchronously iterate and process the entire bucket in `BlockValidatorUpdates`/`DequeueAllMatureUBDQueue`, with no cap and no way for the chain to spread the work across multiple blocks. This can inflate `EndBlocker` duration well beyond normal bounds, directly risking block-production delay (matching the accepted "block delay beyond 2.5 seconds" impact) or, at extreme scale, non-determinism/timeouts across validators.

### Likelihood Explanation
The attack requires only the ability to submit `MsgDelegate` followed by `MsgUndelegate`/`MsgBeginRedelegate` (or the equivalent staking precompile calls from EVM, e.g. `undelegate`/`undelegateWithAuthorization`), which are available to any unprivileged account. There is no minimum bond/delegation amount enforced in this codebase preventing dust delegations, and completion timestamps are coarse enough (all sharing the same `ctx.BlockHeader().Time + UnbondingTime`) that many independently-created entries naturally collapse into a single timeslice bucket, making the attack straightforward to execute at scale (bounded mainly by the attacker's own transaction throughput and the chain's per-block tx/gas limits, not by any staking-specific defense).

### Recommendation
- Introduce a per-block processing budget for `DequeueAllMatureUBDQueue`/`DequeueAllMatureRedelegationQueue` similar to `MaxVotesProcessedPerBlock` in `x/gov`, with cursor-based resumable processing across blocks.
- Enforce a minimum delegation/undelegation amount to make dust-queue-flooding economically infeasible.
- Consider bounding the total number of unbonding/redelegation entries permitted to mature within the same timeslice bucket, independent of the existing per-(delegator,validator) `MaxEntries` limit.

### Proof of Concept
Conceptual PoC (not executed):
1. From a single or a few funded accounts, submit a large number of `MsgDelegate` transactions of minimal amount (e.g. 1 `usei`) to many different validators (or reuse validators with many distinct delegator addresses funded via a faucet loop).
2. In the same block (or blocks close enough that `UnbondingTime` completion times coincide), submit `MsgUndelegate` for each of those delegations. Because `completionTime := ctx.BlockHeader().Time.Add(k.UnbondingTime(ctx))` is deterministic per block, all these entries land in the same UBD queue timeslice: [4](#0-3) .
3. When that block time is reached, `BlockValidatorUpdates` calls `DequeueAllMatureUBDQueue` and iterates the entire bucket synchronously in `EndBlock`, with no per-block cap: [8](#0-7) , inflating `EndBlocker` duration proportionally to the number of injected entries.

### Citations

**File:** sei-cosmos/x/staking/keeper/val_state_change.go (L15-57)
```go
// BlockValidatorUpdates calculates the ValidatorUpdates for the current block
// Called in each EndBlock
func (k Keeper) BlockValidatorUpdates(ctx sdk.Context) []abci.ValidatorUpdate {
	// Calculate validator set changes.
	//
	// NOTE: ApplyAndReturnValidatorSetUpdates has to come before
	// UnbondAllMatureValidatorQueue.
	// This fixes a bug when the unbonding period is instant (is the case in
	// some of the tests). The test expected the validator to be completely
	// unbonded after the Endblocker (go from Bonded -> Unbonding during
	// ApplyAndReturnValidatorSetUpdates and then Unbonding -> Unbonded during
	// UnbondAllMatureValidatorQueue).
	validatorUpdates, err := k.ApplyAndReturnValidatorSetUpdates(ctx)
	if err != nil {
		panic(err)
	}

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

**File:** sei-cosmos/x/staking/keeper/delegation.go (L352-366)
```go
// InsertUBDQueue inserts an unbonding delegation to the appropriate timeslice
// in the unbonding queue.
func (k Keeper) InsertUBDQueue(ctx sdk.Context, ubd types.UnbondingDelegation,
	completionTime time.Time,
) {
	dvPair := types.DVPair{DelegatorAddress: ubd.DelegatorAddress, ValidatorAddress: ubd.ValidatorAddress}

	timeSlice := k.GetUBDQueueTimeSlice(ctx, completionTime)
	if len(timeSlice) == 0 {
		k.SetUBDQueueTimeSlice(ctx, completionTime, []types.DVPair{dvPair})
	} else {
		timeSlice = append(timeSlice, dvPair)
		k.SetUBDQueueTimeSlice(ctx, completionTime, timeSlice)
	}
}
```

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

**File:** sei-cosmos/x/staking/keeper/delegation.go (L826-858)
```go
// Undelegate unbonds an amount of delegator shares from a given validator. It
// will verify that the unbonding entries between the delegator and validator
// are not exceeded and unbond the staked tokens (based on shares) by creating
// an unbonding object and inserting it into the unbonding queue which will be
// processed during the staking EndBlocker.
func (k Keeper) Undelegate(
	ctx sdk.Context, delAddr sdk.AccAddress, valAddr sdk.ValAddress, sharesAmount sdk.Dec,
) (time.Time, error) {
	validator, found := k.GetValidator(ctx, valAddr)
	if !found {
		return time.Time{}, types.ErrNoDelegatorForAddress
	}

	if k.HasMaxUnbondingDelegationEntries(ctx, delAddr, valAddr) {
		return time.Time{}, types.ErrMaxUnbondingDelegationEntries
	}

	returnAmount, err := k.Unbond(ctx, delAddr, valAddr, sharesAmount)
	if err != nil {
		return time.Time{}, err
	}

	// transfer the validator tokens to the not bonded pool
	if validator.IsBonded() {
		k.bondedTokensToNotBonded(ctx, returnAmount)
	}

	completionTime := ctx.BlockHeader().Time.Add(k.UnbondingTime(ctx))
	ubd := k.SetUnbondingDelegationEntry(ctx, delAddr, valAddr, ctx.BlockHeight(), completionTime, returnAmount)
	k.InsertUBDQueue(ctx, ubd, completionTime)

	return completionTime, nil
}
```

**File:** sei-cosmos/x/staking/keeper/msg_server.go (L311-341)
```go
// Undelegate defines a method for performing an undelegation from a delegate and a validator
func (k msgServer) Undelegate(goCtx context.Context, msg *types.MsgUndelegate) (*types.MsgUndelegateResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	addr, err := sdk.ValAddressFromBech32(msg.ValidatorAddress)
	if err != nil {
		return nil, err
	}
	delegatorAddress, err := sdk.AccAddressFromBech32(msg.DelegatorAddress)
	if err != nil {
		return nil, err
	}
	shares, err := k.ValidateUnbondAmount(
		ctx, delegatorAddress, addr, msg.Amount.Amount,
	)
	if err != nil {
		return nil, err
	}

	bondDenom := k.BondDenom(ctx)
	if msg.Amount.Denom != bondDenom {
		return nil, sdkerrors.Wrapf(
			sdkerrors.ErrInvalidRequest, "invalid coin denomination: got %s, expected %s", msg.Amount.Denom, bondDenom,
		)
	}

	completionTime, err := k.Keeper.Undelegate(ctx, delegatorAddress, addr, shares)
	if err != nil {
		return nil, err
	}

```

**File:** sei-cosmos/x/gov/abci.go (L15-36)
```go
// MaxVotesProcessedPerBlock is the governance record-work budget shared by delegation updates, backfill, tallying, and cleanup.
const MaxVotesProcessedPerBlock = 1000

// minTallyCleanupVotesPerBlock reserves part of the budget for completed tally archives.
const minTallyCleanupVotesPerBlock = 100

// BeginBlocker freezes electorates for proposal deadlines strictly between consecutive block times.
func BeginBlocker(ctx sdk.Context, keeper keeper.Keeper) {
	keeper.CaptureGapTallyBoundary(ctx)
}

// EndBlocker expires governance proposals and advances their tally work.
func EndBlocker(ctx sdk.Context, keeper keeper.Keeper) {
	endBlockerStart := time.Now()
	defer func() {
		govMetrics.endBlockerDuration.Record(ctx.Context(), time.Since(endBlockerStart).Seconds())
	}()
	if !keeper.IncrementalTallyEnabled(ctx) {
		legacyEndBlocker(ctx, keeper)
		return
	}
	keeper.CaptureExactTallyBoundary(ctx)
```
