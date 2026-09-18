[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [4](#0-3) [4](#0-3) [3](#0-2) [7](#0-6)

### Citations

**File:** x/oracle/abci.go (L35-44)
```go
		i := 0
		for ; iterator.Valid() && i < int(maxValidators); iterator.Next() {
			validator := k.StakingKeeper.Validator(ctx, iterator.Value())
			// Exclude not bonded validator
			if validator.IsBonded() {
				valAddr := validator.GetOperator()
				validatorClaimMap[valAddr.String()] = types.NewClaim(validator.GetConsensusPower(powerReduction), 0, 0, valAddr, false)
				i++
			}
		}
```

**File:** x/oracle/abci.go (L54-57)
```go
		// Organize votes to ballot by denom
		// NOTE: **Filter out inactive or jailed validators**
		// NOTE: **Make abstain votes to have zero vote power**
		voteMap := k.OrganizeBallotByDenom(ctx, validatorClaimMap)
```

**File:** x/oracle/keeper/ballot.go (L18-21)
```go
	aggregateHandler := func(voterAddr sdk.ValAddress, vote types.AggregateExchangeRateVote) (stop bool) {
		// organize ballot only for the active validators
		claim, ok := validatorClaimMap[vote.Voter]

```

**File:** x/oracle/keeper/slash.go (L39-53)
```go
		if validVoteRate.LT(minValidPerWindow) {
			validator := k.StakingKeeper.Validator(ctx, operator)
			if validator.IsBonded() && !validator.IsJailed() {
				consAddr, err := validator.GetConsAddr()
				if err != nil {
					panic(err)
				}

				k.StakingKeeper.Slash(
					ctx, consAddr,
					distributionHeight, validator.GetConsensusPower(powerReduction), slashFraction,
				)
				k.StakingKeeper.Jail(ctx, consAddr)
				oracleKeeperMetrics.validatorSlashedTotal.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("validator", consAddr.String()), attribute.String("type", "oracle")))
			}
```

**File:** x/oracle/keeper/slash_test.go (L96-107)
```go
	// Case 4, slash jailed validator
	validator, _ = input.StakingKeeper.GetValidator(input.Ctx, testutils.ValAddrs[0])
	validator.Status = stakingtypes.Bonded
	validator.Jailed = true
	validator.Tokens = amt
	input.StakingKeeper.SetValidator(input.Ctx, validator)

	input.OracleKeeper.SetVotePenaltyCounter(input.Ctx, testutils.ValAddrs[0], uint64(votePeriodsPerWindow-minValidVotes+1), 0, 0)
	input.OracleKeeper.SlashAndResetCounters(input.Ctx)
	validator, _ = input.StakingKeeper.GetValidator(input.Ctx, testutils.ValAddrs[0])
	require.Equal(t, amt, validator.Tokens)
}
```

**File:** x/oracle/tally.go (L13-35)
```go
func Tally(_ sdk.Context, pb types.ExchangeRateBallot, rewardBand sdk.Dec, validatorClaimMap map[string]types.Claim) (weightedMedian sdk.Dec) {
	weightedMedian = pb.WeightedMedianWithAssertion()

	standardDeviation := pb.StandardDeviation(weightedMedian)
	rewardSpread := weightedMedian.Mul(rewardBand.QuoInt64(2))

	if standardDeviation.GT(rewardSpread) {
		rewardSpread = standardDeviation
	}

	for _, vote := range pb {
		// Filter ballot winners
		key := vote.Voter.String()
		claim := validatorClaimMap[key]
		if vote.ExchangeRate.GTE(weightedMedian.Sub(rewardSpread)) &&
			rewardSpread.GTE(vote.ExchangeRate.Sub(weightedMedian)) {

			claim.Weight += vote.Power
			claim.WinCount++
		}
		claim.DidVote = true
		validatorClaimMap[key] = claim
	}
```
