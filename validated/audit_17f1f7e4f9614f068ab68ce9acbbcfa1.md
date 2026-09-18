### Title
Unbounded processing of matured unbonding-delegation/redelegation queues in `staking` `EndBlock` allows cheap queue-growth to force disproportionate `EndBlock` costs and risk block delay - (File: `sei-cosmos/x/staking/keeper/val_state_change.go`)

### Summary
The `KangarooVault`/`LiquidityPool` bug class is: an unbounded, sequentially-processed queue that any low-cost caller can grow, forcing whoever eventually triggers processing (or the protocol itself) to pay disproportionately high processing cost. The Sei staking module contains a structurally identical pattern: unbonding delegations and redelegations are queued by cheap, per-account `Undelegate`/`BeginRedelegate` transactions, and *all* matured entries are dequeued and processed unconditionally, with no count cap, in a single `EndBlock` call.

### Finding Description
`Keeper.Undelegate` inserts a `DVPair` into the UBD queue for a completion time, gated only by a per-`(delegator, validator)` pair limit (`HasMaxUnbondingDelegationEntries`) [1](#0-0) . That per-pair cap does **not** bound the total number of `(delegator, validator)` pairs that can exist system-wide — an attacker can create many delegator addresses, delegate a minimal (even dust) amount to many validators, and then submit cheap `Undelegate` transactions from each, all targeting the same completion time (all created within the same unbonding-time window naturally align because `completionTime = now + UnbondingTime` is deterministic).

At every block, `BlockValidatorUpdates` unconditionally drains the *entire* matured portion of both the UBD queue and the redelegation queue and processes every entry in a single pass with no batch/budget limit: [2](#0-1) [3](#0-2) 

`DequeueAllMatureUBDQueue` itself has no size cap — it iterates and deletes every timeslice up to `ctx.BlockHeader().Time` in one call, and `CompleteUnbonding` then performs a bank transfer for every entry: [4](#0-3) [5](#0-4) 

Notably, the Cosmos SDK `gov` module's analogous "Proposal Processing Queue" explicitly documents a bounded, budgeted per-block scan to avoid unbounded `EndBlock` work when a queue backs up [6](#0-5) , but no equivalent budget/limit exists for the staking UBD/redelegation queues — the entire matured backlog is always drained in one shot, mirroring exactly the sequential/no-cap queue-draining pattern described in the report.

This is reachable purely from unprivileged transaction senders (including via the staking Cosmos precompile callable from EVM, as shown by `staking.undelegate`/`staking.redelegate` usage in the precompile test suite) — no validator or governance privilege is required [7](#0-6) .

### Impact Explanation
An attacker who funds many accounts with small delegations (bounded only by the chain's minimum delegation amount, which can be as low as 1 `usei`) to many validators, then issues cheap `Undelegate`/`BeginRedelegate` transactions from each, can inflate the maturing queue to an arbitrarily large size for a specific future block height. When that block's `EndBlock` runs, `BlockValidatorUpdates` must synchronously process every one of those entries (bank transfers, event emission, KV store deletes) with no cap, directly slowing down `EndBlock` execution for that block. This can push block production time past acceptable bounds (risking the >2.5s block-delay threshold), and validators/proposers bear the cost of processing the attacker-created backlog rather than the attacker who created it cheaply — the same "victim pays for attacker's cheap queue growth" dynamic as the reported bug.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to fund many small delegations across a large number of `(delegator, validator)` pairs (bounded by `MaxEntries` per pair, not globally), incurring real gas and minimal principal costs, and to time the transactions so that a large batch matures within the same block window (natural, since `UnbondingTime` is fixed and deterministic). No special privilege or validator control is needed, and validator count / delegator address generation are essentially free, making the attack economically feasible at meaningful scale over the unbonding period.

### Recommendation
Introduce a bounded per-block processing budget/limit for `DequeueAllMatureUBDQueue` and `DequeueAllMatureRedelegationQueue`/`CompleteUnbonding`/`CompleteRedelegation`, analogous to the "vote-processing budget" already used by the `gov` module's proposal queue, so that a single `EndBlock` call cannot be forced to process an unbounded number of matured entries. Entries exceeding the budget should remain queued and be processed in subsequent blocks rather than draining the entire backlog synchronously.

### Proof of Concept
1. Create N distinct delegator accounts (funded with minimal `usei` each).
2. From each account, delegate a small amount to M distinct validators, then immediately call `Undelegate` (or `BeginRedelegate`) up to the per-pair `MaxEntries` limit, repeating across many `(delegator, validator)` pairs — see `HasMaxUnbondingDelegationEntries` gating in `sei-cosmos/x/staking/keeper/delegation.go` — to build a very large aggregate backlog of `DVPair`/`DVVTriplet` entries all completing near the same `ctx.BlockHeader().Time` (deterministic offset via `UnbondingTime`).
3. Wait for the shared completion time; at that block's `EndBlock`, `BlockValidatorUpdates` calls `DequeueAllMatureUBDQueue`/`DequeueAllMatureRedelegationQueue` and processes every entry in a single unbounded loop, as shown in `sei-cosmos/x/staking/keeper/val_state_change.go` lines 35-91, causing that block's `EndBlock` execution time to scale linearly with the attacker-created backlog size rather than being capped.

Note: I was not able to fully verify whether any global/system-wide cap (as opposed to the per-pair `MaxEntries`) exists on the total number of outstanding UBD/redelegation queue entries elsewhere in the codebase (e.g., mempool-level rate limiting of `Undelegate` messages); if such a cap exists, it would reduce the practical severity of this analog. This should be verified with a full Devin session against the complete `x/staking` module and any mempool/ante-level throttling.

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

**File:** sei-cosmos/x/staking/keeper/val_state_change.go (L59-91)
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

		ctx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeCompleteRedelegation,
				sdk.NewAttribute(sdk.AttributeKeyAmount, balances.String()),
				sdk.NewAttribute(types.AttributeKeyDelegator, dvvTriplet.DelegatorAddress),
				sdk.NewAttribute(types.AttributeKeySrcValidator, dvvTriplet.ValidatorSrcAddress),
				sdk.NewAttribute(types.AttributeKeyDstValidator, dvvTriplet.ValidatorDstAddress),
			),
		)
	}
```

**File:** sei-cosmos/x/gov/spec/02_state.md (L136-154)
```markdown
## Proposal Processing Queue

**Store:**

- `ProposalProcessingQueue`: A queue `queue[proposalID]` containing all the
  `ProposalIDs` of proposals that reached `MinDeposit`. During each `EndBlock`,
  proposals that have reached the end of their voting period are advanced within
  the block's vote-processing budget.

To process a finished proposal, the application tallies the votes, computes the
votes of each validator and checks if every validator in the validator set has
voted. If the proposal is accepted, deposits are refunded. Finally, the proposal
content `Handler` is executed.

Expired proposals remain queue-ordered. If an earlier proposal does not finish
within the block's vote-processing budget, the queue scan stops and later proposals
wait for the earlier tally to complete. This also prevents the block from initializing
validator snapshots for an unbounded number of proposals after the vote budget is
exhausted.
```

**File:** integration_test/precompile_tests/precompiles/staking.spec.ts (L129-171)
```typescript
        it('undelegate creates an unbonding entry and funds return after unbonding_time', async () => {
            const half = DELEGATE_USEI; // one of the two 1-SEI delegations above

            // Legacy scar tissue: undelegate gas fluctuates block-to-block on CI
            // (staking queue writes) — give it a generous explicit limit up front.
            const tx = await (staking.connect(delegator.wallet) as ethers.Contract).undelegate(
                validators[0],
                half,
                { gasLimit: 2_000_000 },
            );
            const receipt = await tx.wait();
            expect(receipt!.status, 'undelegate tx must succeed').to.equal(1);

            // Baseline AFTER the undelegate (its gas and implicit reward
            // withdrawal already applied) but before the 10s maturity.
            const balanceBeforeMaturity = await bankBalance(delegator.seiAddress());

            // The devnet's unbonding_time is 10s — poll for the entry before it
            // matures (the RPC node can lag the block that included the tx).
            // NB: the component is named `entries`, which collides with
            // Array.prototype.entries on ethers' Result — use getValue().
            const entries = await waitUntil(
                async () => {
                    const ubd = await staking.unbondingDelegation(
                        delegator.address,
                        validators[0],
                    );
                    const list = ubd.getValue('entries') as ethers.Result;
                    return list.length > 0 ? list : null;
                },
                { timeoutMs: 8_000, intervalMs: 200, label: 'unbonding entry before maturity' },
            );
            expect(BigInt(entries[0].balance), 'unbonding entry carries the amount').to.equal(half);

            // After ~10s the staking EndBlocker credits the delegator's bank balance.
            await waitUntil(
                async () => {
                    const b = await bankBalance(delegator.seiAddress());
                    return b >= balanceBeforeMaturity + half ? b : null;
                },
                { timeoutMs: 45_000, label: 'unbonded funds returned to bank balance' },
            );
        });
```
