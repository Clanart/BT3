### Title
Unbounded deposit iteration in gov `EndBlocker` allows an attacker to stall block processing - ([File: sei-cosmos/x/gov/keeper/deposit.go])

### Summary
`RefundDeposits` and `DeleteDeposits` iterate over every deposit record stored for a proposal with no cap on the number of iterations, and are invoked synchronously from the governance `EndBlocker` whenever a proposal's voting period completes. [1](#0-0) [2](#0-1)  Any unprivileged account can permissionlessly grow the deposit set for a proposal to an arbitrary size by submitting many small `MsgDeposit` transactions from many different addresses (deposits are keyed per-depositor, `types.DepositKey(proposalID, depositor)`, so each new address creates a new, permanently-iterated record until refunded or deleted). [3](#0-2) [4](#0-3) 

### Finding Description
When a proposal's tally completes in `EndBlocker`, the code either burns or refunds all of that proposal's deposits by calling `keeper.DeleteDeposits` or `keeper.RefundDeposits`, both of which use `IterateDeposits`, a full unpaginated KV-store prefix scan with no per-block or per-call bound. [5](#0-4) [6](#0-5) 

This is the exact bug class from the referenced report: an adversary makes a large number of small deposits so that a later loop over "every deposit" becomes unboundedly expensive. Notably, the same module already recognizes and mitigates this exact risk for *votes*: the incremental tally logic explicitly caps per-block vote processing at `MaxVotesProcessedPerBlock = 1000` specifically so that an unbounded number of vote records cannot stall a block. [7](#0-6) [8](#0-7)  No equivalent budget exists for the deposit-refund/deletion path invoked in the very same `EndBlocker` call, even though `AddDeposit` places no minimum-amount restriction on individual deposits and permits any number of distinct depositor addresses to each hold a deposit record on a single proposal. [9](#0-8) 

Each iteration inside `RefundDeposits` additionally performs a bank-module state check and, if eligible, a `SendCoinsFromModuleToAccount` call plus a KV store delete, all inside the deterministic `EndBlock` phase which is not gas-metered like a normal transaction, so it cannot be capped or reverted by gas exhaustion in the same way a user transaction would be — it simply runs to completion, consuming wall-clock time proportional to the number of attacker-created deposit records. [10](#0-9) 

### Impact Explanation
Because the deposit-processing loop runs unconditionally and synchronously inside `EndBlocker` for every proposal that finishes its voting period, an attacker who fans out enough small deposits (from disposable, freshly-associated accounts) across a governance proposal can inflate the wall-clock cost of `EndBlocker` at the block where that proposal's tally completes. This directly threatens the "block delay beyond 2.5 seconds" impact criterion, since unlike the vote-tally path there is no batching/checkpointing mechanism to spread the work across multiple blocks — the entire deposit set for the proposal is processed in a single `EndBlock` call.

### Likelihood Explanation
Submitting `MsgDeposit` (including via the gov precompile `DepositMethod`) is fully permissionless, and `AddDeposit` enforces no minimum deposit amount per call and no cap on the number of distinct depositor records per proposal. [11](#0-10)  An attacker only needs enough SEI to cover minimal deposit amounts and transaction fees for many transactions from many addresses, which is a standard low-cost operation.

### Recommendation
Apply the same incremental/budgeted processing pattern already used for vote tallying (`MaxVotesProcessedPerBlock`) to deposit refund/deletion: process a bounded number of deposit records per `EndBlock` call and carry over remaining work via a cursor, similar to `TallyIncremental`/`CleanupTallyVotes`, or gate deposit acceptance with a maximum number of distinct depositors per proposal and/or a minimum deposit amount to make deposit-record inflation costly.

### Proof of Concept
1. Create many EVM/Sei accounts and fund each with a minimal SEI balance.
2. Submit a `MsgSubmitProposal` and one large deposit to activate the voting period.
3. From each of the many accounts, submit `MsgDeposit` (native or via the gov precompile `DepositMethod`) for a minimal, non-zero amount to the same proposal, creating one `Deposit` record per address (`DepositKey(proposalID, depositor)`).
4. Once the proposal's voting period ends, `EndBlocker` calls `RefundDeposits` (or `DeleteDeposits` for a burned/rejected/failed expedited-min-deposit case), which iterates unboundedly over all deposit records created in step 3, in a single synchronous `EndBlock` pass with no per-block cap, unlike the explicitly capped vote-tally path.

### Citations

**File:** sei-cosmos/x/gov/keeper/deposit.go (L24-31)
```go
// SetDeposit sets a Deposit to the gov store
func (keeper Keeper) SetDeposit(ctx sdk.Context, deposit types.Deposit) {
	store := ctx.KVStore(keeper.storeKey)
	bz := keeper.cdc.MustMarshal(&deposit)
	depositor := sdk.MustAccAddressFromBech32(deposit.Depositor)

	store.Set(types.DepositKey(deposit.ProposalId, depositor), bz)
}
```

**File:** sei-cosmos/x/gov/keeper/deposit.go (L53-68)
```go
// DeleteDeposits deletes all the deposits on a specific proposal without refunding them
func (keeper Keeper) DeleteDeposits(ctx sdk.Context, proposalID uint64) {
	store := ctx.KVStore(keeper.storeKey)

	keeper.IterateDeposits(ctx, proposalID, func(deposit types.Deposit) bool {
		err := keeper.bankKeeper.BurnCoins(ctx, types.ModuleName, deposit.Amount)
		if err != nil {
			panic(err)
		}

		depositor := sdk.MustAccAddressFromBech32(deposit.Depositor)

		store.Delete(types.DepositKey(proposalID, depositor))
		return false
	})
}
```

**File:** sei-cosmos/x/gov/keeper/deposit.go (L88-104)
```go
// IterateDeposits iterates over the all the proposals deposits and performs a callback function
func (keeper Keeper) IterateDeposits(ctx sdk.Context, proposalID uint64, cb func(deposit types.Deposit) (stop bool)) {
	store := ctx.KVStore(keeper.storeKey)
	iterator := sdk.KVStorePrefixIterator(store, types.DepositsKey(proposalID))

	defer func() { _ = iterator.Close() }()

	for ; iterator.Valid(); iterator.Next() {
		var deposit types.Deposit

		keeper.cdc.MustUnmarshal(iterator.Value(), &deposit)

		if cb(deposit) {
			break
		}
	}
}
```

**File:** sei-cosmos/x/gov/keeper/deposit.go (L106-151)
```go
// AddDeposit adds or updates a deposit of a specific depositor on a specific proposal
// Activates voting period when appropriate
func (keeper Keeper) AddDeposit(ctx sdk.Context, proposalID uint64, depositorAddr sdk.AccAddress, depositAmount sdk.Coins) (bool, error) {
	// Checks to see if proposal exists
	proposal, ok := keeper.GetProposal(ctx, proposalID)
	if !ok {
		return false, sdkerrors.Wrapf(types.ErrUnknownProposal, "%d", proposalID)
	}

	// Check if proposal is still depositable
	if (proposal.Status != types.StatusDepositPeriod) && (proposal.Status != types.StatusVotingPeriod) {
		return false, sdkerrors.Wrapf(types.ErrInactiveProposal, "%d", proposalID)
	}
	if keeper.IncrementalTallyEnabled(ctx) && proposal.Status == types.StatusVotingPeriod {
		if proposal.VotingEndTime.Before(ctx.BlockTime()) || keeper.voteDelegationSnapshotFrozen(ctx, proposal) {
			return false, sdkerrors.Wrapf(types.ErrInactiveProposal, "%d", proposalID)
		}
	}

	// update the governance module's account coins pool
	err := keeper.bankKeeper.SendCoinsFromAccountToModule(ctx, depositorAddr, types.ModuleName, depositAmount)
	if err != nil {
		return false, err
	}

	// Update proposal
	proposal.TotalDeposit = proposal.TotalDeposit.Add(depositAmount...)
	keeper.SetProposal(ctx, proposal)

	// Check if deposit has provided sufficient total funds to transition the proposal into the voting period
	activatedVotingPeriod := false

	if proposal.Status == types.StatusDepositPeriod && proposal.TotalDeposit.IsAllGTE(keeper.GetDepositParams(ctx).GetMinimumDeposit(proposal.IsExpedited)) {
		keeper.ActivateVotingPeriod(ctx, proposal)

		activatedVotingPeriod = true
	}

	// Add or update deposit object
	deposit, found := keeper.GetDeposit(ctx, proposalID, depositorAddr)

	if found {
		deposit.Amount = deposit.Amount.Add(depositAmount...)
	} else {
		deposit = types.NewDeposit(proposalID, depositorAddr, depositAmount)
	}
```

**File:** sei-cosmos/x/gov/keeper/deposit.go (L169-192)
```go
// RefundDeposits refunds deposits whose recipients can receive funds and deletes
// their records. Deposits for unpayable recipients remain recorded and backed by
// the governance module balance.
func (keeper Keeper) RefundDeposits(ctx sdk.Context, proposalID uint64) {
	store := ctx.KVStore(keeper.storeKey)

	keeper.IterateDeposits(ctx, proposalID, func(deposit types.Deposit) bool {
		depositor := sdk.MustAccAddressFromBech32(deposit.Depositor)
		if keeper.bankKeeper.BlockedAddr(depositor) || !keeper.bankKeeper.CanSendTo(ctx, depositor) {
			// Retain the deposit so its record continues to account for the backing
			// module balance. Recovering a permanently unreceivable deposit requires
			// a migration.
			return false
		}

		err := keeper.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, depositor, deposit.Amount)
		if err != nil {
			panic(err)
		}

		store.Delete(types.DepositKey(proposalID, depositor))
		return false
	})
}
```

**File:** sei-cosmos/x/gov/abci.go (L15-19)
```go
// MaxVotesProcessedPerBlock is the governance record-work budget shared by delegation updates, backfill, tallying, and cleanup.
const MaxVotesProcessedPerBlock = 1000

// minTallyCleanupVotesPerBlock reserves part of the budget for completed tally archives.
const minTallyCleanupVotesPerBlock = 100
```

**File:** sei-cosmos/x/gov/abci.go (L66-77)
```go
	remainingRecords := MaxVotesProcessedPerBlock
	remainingRecords -= keeper.CleanupTallyVotes(ctx, minTallyCleanupVotesPerBlock)
	if remainingRecords == 0 {
		return
	}

	// fetch active proposals whose voting periods have ended (are passed the block time)
	keeper.IterateActiveProposalsQueue(ctx, ctx.BlockHeader().Time, func(proposal types.Proposal) bool {
		var tagValue, logMsg string

		complete, processed, passes, burnDeposits, tallyResults := keeper.TallyIncremental(ctx, proposal, remainingRecords)
		remainingRecords -= processed
```

**File:** sei-cosmos/x/gov/abci.go (L83-93)
```go
		// If an expedited proposal fails, we do not want to update
		// the deposit at this point since the proposal is converted to regular.
		// As a result, the deposits are either deleted or refunded in all casses
		// EXCEPT when an expedited proposal fails.
		if !proposal.IsExpedited || passes {
			if burnDeposits {
				keeper.DeleteDeposits(ctx, proposal.ProposalId)
			} else {
				keeper.RefundDeposits(ctx, proposal.ProposalId)
			}
		}
```

**File:** precompiles/gov/gov.go (L207-208)
```go
	case DepositMethod:
		return p.deposit(ctx, method, caller, args, value, hooks, evm)
```
