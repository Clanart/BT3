### Title
DoS from unbounded per-delegation slash-event iteration in reward/withdrawal calculation - (File: `sei-cosmos/x/distribution/keeper/delegation.go`)

### Summary
The `Staking.sol` report describes a DoS where a per-user reward calculation loop grows unbounded with the number of state-updating events since the user's last interaction, eventually exceeding gas limits. Sei's distribution module has the analogous, unmitigated pattern: `calculateDelegationRewards()` iterates every validator slash event between a delegation's starting height and the current height on every reward/withdrawal path, with no cap on the number of slash events scanned, unlike the referenced fix which introduced `MAX_STAKING_CONDITIONS_LIMIT`.

### Finding Description
`calculateDelegationRewards` in [1](#0-0)  calls `k.IterateValidatorSlashEventsBetween(ctx, del.GetValidatorAddr(), startingHeight, endingHeight, ...)` to replay every slash event recorded for that validator between the delegator's `startingInfo.Height` and the current block height, computing intermediate reward periods for each one. There is no upper bound on the number of slash events processed in a single call — the loop scales linearly with however many `ValidatorSlashEvent` records accumulated for the validator while the delegator was inactive, exactly the unbounded-iteration pattern flagged in the report (compare to the fixed `_calculateRewards()`/`maxStakingConditions` cap in `Staking.sol`).

This function is on the critical path for:
- Native `MsgWithdrawDelegatorReward` / any delegation change (delegate more, undelegate, redelegate — all trigger `BeforeDelegationSharesModified`/`initializeDelegation`, which withdraws pending rewards first).
- The EVM distribution precompile's `withdrawDelegationRewards`, `withdrawMultipleDelegationRewards`, and `rewards()` query, all directly reachable by an ordinary EVM transaction/call as shown in [2](#0-1)  and [3](#0-2) .

Each downtime-slash occurrence creates a new `ValidatorSlashEvent` for the validator, via `k.sk.Slash(...)` called from `SlashJailAndUpdateSigningInfo` in [4](#0-3) , which in turn invokes `k.BeforeValidatorSlashed` in `staking` (`Slash`) in [5](#0-4) . Unlike double-sign infractions, downtime slashing does not tombstone the validator — the validator can be jailed and later unjailed repeatedly (subject to `DowntimeJailDuration`), so a validator experiencing repeated downtime over a long period can accumulate an arbitrarily large number of slash events. Any delegator to that validator who does not withdraw/re-delegate/undelegate during that window must, on their next interaction, pay the full linear cost of replaying every one of those slash events in a single atomic call.

### Impact Explanation
If a validator's slash-event count grows large enough (through repeated downtime jail/unjail cycles over an extended period), a delegator's `calculateDelegationRewards` call can become gas-intensive enough to fail within a single transaction's gas limit. Because this same code path is invoked not just for reward withdrawal but for *any* delegation modification (undelegate, redelegate, or adding stake), a sufficiently large backlog of slash events can effectively freeze a delegator's ability to unstake or claim rewards from that validator — a denial of service on the user's funds, reachable purely through the EVM distribution precompile or plain `MsgWithdrawDelegatorReward`/`MsgUndelegate` transactions, with no operator/governance action required.

### Likelihood Explanation
Likelihood is lower than the original finding because it requires many downtime-slash events to accumulate against a single validator over a long time window while at least one delegator never interacts with that delegation, and slash-fraction/jailing parameters (`SlashFractionDowntime`, `DowntimeJailDuration`, `SignedBlocksWindow`) throttle how quickly this can occur. There is no explicit cap analogous to `maxStakingConditions`/`MAX_STAKING_CONDITIONS_LIMIT`, so the bound is purely a function of elapsed chain time and validator downtime frequency, making this a real but slow-building risk rather than an immediately triggerable exploit.

### Recommendation
Add an explicit bound on the number of slash events processed per `calculateDelegationRewards` call (or per block), or periodically compact/checkpoint slash-event history for long-inactive delegations, mirroring the `MAX_STAKING_CONDITIONS_LIMIT` mitigation adopted for `Staking.sol`. Alternatively, expose a keeper/precompile function that lets a delegator incrementally advance through slash events over multiple transactions instead of requiring a single all-at-once replay.

### Proof of Concept
Conceptual (not independently executed):
1. Delegate to a validator, then leave the delegation untouched.
2. Have that validator repeatedly go offline long enough to be jailed for downtime, then unjail, over many cycles across a long span of blocks — each cycle appends a new `ValidatorSlashEvent` via `sk.Slash` ( [6](#0-5) ).
3. After enough cycles accumulate, call `withdrawDelegationRewards(validator)` via the EVM distribution precompile ( [2](#0-1) ) or send `MsgWithdrawDelegatorReward`/`MsgUndelegate`.
4. Observe `calculateDelegationRewards`'s `IterateValidatorSlashEventsBetween` loop ( [7](#0-6) ) consuming gas proportional to the accumulated slash-event count, eventually exceeding the caller's gas limit and reverting the withdrawal/undelegate attempt.

### Citations

**File:** sei-cosmos/x/distribution/keeper/delegation.go (L159-197)
```go
func (k Keeper) calculateDelegationRewards(ctx sdk.Context, val stakingtypes.ValidatorI, del stakingtypes.DelegationI, endingPeriod uint64, endingRatio *sdk.DecCoins) (rewards sdk.DecCoins) {
	// fetch starting info for delegation
	startingInfo := k.GetDelegatorStartingInfo(ctx, del.GetValidatorAddr(), del.GetDelegatorAddr())

	if startingInfo.Height == uint64(ctx.BlockHeight()) { //nolint:gosec // block heights are always non-negative
		// started this height, no rewards yet
		return
	}

	startingPeriod := startingInfo.PreviousPeriod
	stake := startingInfo.Stake

	// Iterate through slashes and withdraw with calculated staking for
	// distribution periods. These period offsets are dependent on *when* slashes
	// happen - namely, in BeginBlock, after rewards are allocated...
	// Slashes which happened in the first block would have been before this
	// delegation existed, UNLESS they were slashes of a redelegation to this
	// validator which was itself slashed (from a fault committed by the
	// redelegation source validator) earlier in the same BeginBlock.
	startingHeight := startingInfo.Height
	// Slashes this block happened after reward allocation, but we have to account
	// for them for the stake sanity check below.
	endingHeight := uint64(ctx.BlockHeight()) //nolint:gosec // block heights are always non-negative
	if endingHeight > startingHeight {
		k.IterateValidatorSlashEventsBetween(ctx, del.GetValidatorAddr(), startingHeight, endingHeight,
			func(height uint64, event types.ValidatorSlashEvent) (stop bool) {
				endingPeriod := event.ValidatorPeriod
				if endingPeriod > startingPeriod {
					rewards = rewards.Add(k.calculateDelegationRewardsBetween(ctx, val, startingPeriod, endingPeriod, stake)...)

					// Note: It is necessary to truncate so we don't allow withdrawing
					// more rewards than owed.
					stake = stake.MulTruncate(sdk.OneDec().Sub(event.Fraction))
					startingPeriod = endingPeriod
				}
				return false
			},
		)
	}
```

**File:** precompiles/distribution/distribution.go (L204-205)
```go
	case RewardsMethod:
		return p.rewards(ctx, method, args)
```

**File:** precompiles/distribution/distribution.go (L359-372)
```go
func (p PrecompileExecutor) withdrawDelegationRewards(ctx sdk.Context, method *abi.Method, caller common.Address, args []interface{}, value *big.Int, evm *vm.EVM) (ret []byte, remainingGas uint64, rerr error) {
	if err := p.validateInput(value, args, 1); err != nil {
		return nil, 0, err
	}
	delegator, err := p.getDelegator(ctx, caller)
	if err != nil {
		return nil, 0, err
	}
	validatorAddress := args[0].(string)
	execute := func() (sdk.Int, error) {
		amts, err := p.withdraw(ctx, delegator, validatorAddress)
		return amts.AmountOf(sdk.DefaultBondDenom), err
	}
	return p.withdrawDelegationRewardsFor(ctx, method, caller, validatorAddress, evm, execute)
```

**File:** sei-cosmos/x/slashing/keeper/infractions.go (L127-145)
```go
func (k Keeper) SlashJailAndUpdateSigningInfo(ctx sdk.Context, consAddr sdk.ConsAddress, slashInfo SlashInfo, signInfo types.ValidatorSigningInfo) types.ValidatorSigningInfo {

	ctx.EventManager().EmitEvent(
		sdk.NewEvent(
			types.EventTypeSlash,
			sdk.NewAttribute(types.AttributeKeyAddress, consAddr.String()),
			sdk.NewAttribute(types.AttributeKeyPower, fmt.Sprintf("%d", slashInfo.power)),
			sdk.NewAttribute(types.AttributeKeyReason, types.AttributeValueMissingSignature),
			sdk.NewAttribute(types.AttributeKeyJailed, consAddr.String()),
		),
	)

	// Slashed for missing too many block
	slashingKeeperMetrics.validatorSlashed.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("type", types.AttributeValueMissingSignature), attribute.String("validator", consAddr.String())))
	k.sk.Slash(ctx, consAddr, slashInfo.distributionHeight, slashInfo.power, k.SlashFractionDowntime(ctx))
	k.sk.Jail(ctx, consAddr)
	signInfo.JailedUntil = ctx.BlockHeader().Time.Add(k.DowntimeJailDuration(ctx))
	signInfo.MissedBlocksCounter = 0
	signInfo.IndexOffset = 0
```

**File:** sei-cosmos/x/staking/keeper/slash.go (L31-65)
```go
func (k Keeper) Slash(ctx sdk.Context, consAddr sdk.ConsAddress, infractionHeight int64, power int64, slashFactor sdk.Dec) {

	if slashFactor.IsNegative() {
		panic(fmt.Errorf("attempted to slash with a negative slash factor: %v", slashFactor))
	}

	// Amount of slashing = slash slashFactor * power at time of infraction
	amount := k.TokensFromConsensusPower(ctx, power)
	slashAmountDec := amount.ToDec().Mul(slashFactor)
	slashAmount := slashAmountDec.TruncateInt()

	// ref https://github.com/cosmos/cosmos-sdk/issues/1348

	validator, found := k.GetValidatorByConsAddr(ctx, consAddr)
	if !found {
		// If not found, the validator must have been overslashed and removed - so we don't need to do anything
		// NOTE:  Correctness dependent on invariant that unbonding delegations / redelegations must also have been completely
		//        slashed in this case - which we don't explicitly check, but should be true.
		// Log the slash attempt for future reference (maybe we should tag it too)
		logger.Error(
			"WARNING: ignored attempt to slash a nonexistent validator; we recommend you investigate immediately",
			"validator", consAddr,
		)
		return
	}

	// should not be slashing an unbonded validator
	if validator.IsUnbonded() {
		panic(fmt.Sprintf("should not be slashing unbonded validator: %s", validator.GetOperator()))
	}

	operatorAddress := validator.GetOperator()

	// call the before-modification hook
	k.BeforeValidatorModified(ctx, operatorAddress)
```

**File:** sei-cosmos/x/staking/keeper/slash.go (L116-124)
```go
	if validator.Tokens.IsPositive() {
		effectiveFraction := tokensToBurn.ToDec().QuoRoundUp(validator.Tokens.ToDec())
		// possible if power has changed
		if effectiveFraction.GT(sdk.OneDec()) {
			effectiveFraction = sdk.OneDec()
		}
		// call the before-slashed hook
		k.BeforeValidatorSlashed(ctx, operatorAddress, effectiveFraction)
	}
```
