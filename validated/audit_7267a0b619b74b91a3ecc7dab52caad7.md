### Title
Missing sanity bounds on KIP-71 `gastarget` governance parameter allows a governing-node vote to zero it, causing a division-by-zero panic in base-fee computation - (File: kaiax/gov/param.go, params/kip71_config.go)

### Summary
The `kip71.gastarget` governance parameter (and several other KIP-71/reward parameters) uses `noopFormatChecker`, which accepts any `uint64` value including `0`, when it is set via a header-governance vote. This value is later used as a divisor in `KIP71Config.NextMagmaBlockBaseFee`, without a zero-check, unlike `BaseFeeDenominator` which does have an explicit zero-fallback.

### Finding Description
`gov.Params[Kip71GasTarget]` is defined with `FormatChecker: noopFormatChecker`, which always returns `true` for any canonicalized `uint64` value: [1](#0-0) 

`noopFormatChecker` itself performs no validation at all: [2](#0-1) 

The consistency check performed on votes for `Kip71GasTarget` in `checkConsistency` also performs no value-range validation; it simply falls into the catch-all "no more checks" case alongside `Kip71BaseFeeDenominator` and `Kip71MaxBlockGasUsedForBaseFee`: [3](#0-2) 

Only `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` receive relational validation (lower ≤ upper) in that switch statement: [4](#0-3) 

This unvalidated `GasTarget` value is later consumed in `NextMagmaBlockBaseFee`, the deterministic per-block base-fee calculation run by every node during header preparation and verification: [5](#0-4) 

Note that the code explicitly guards against `BaseFeeDenominator == 0` by falling back to `64` (`params/kip71_config.go:71-76`), demonstrating that the developers were aware zero-value governance parameters are a real risk — but the equivalent guard is missing for `gasTarget`. If `gasTarget == 0` and `parentGasUsed > 0` (the overwhelmingly common case), execution enters the `parentGasUsed > gasTarget` branch and computes `y := x.Div(x, new(big.Int).SetUint64(gasTarget))`, i.e., `big.Int.Div(x, 0)`, which panics in Go's `math/big` package. Since `NextMagmaBlockBaseFee` is called deterministically by every node validating/producing a block after the vote is ratified (an epoch boundary), this crashes all nodes processing that block simultaneously.

This matches the report's underlying bug class: an owner/governing-node-controlled critical parameter with no input validation. The `governance_vote` RPC allows the governing node (in `single` mode) or a council member (in `none` mode) to submit `kip71.gastarget = 0`. The vote passes canonicalization and format-check (`NewVoteData` in `kaiax/gov/headergov/vote.go:29-55` only calls `Canonicalizer` and `FormatChecker`), and passes `checkConsistency` in header verification, so it can be committed to `header.Vote`/`header.Governance` and ratified.

### Impact Explanation
Once ratified, at the start of the next epoch every full node computing/verifying the Magma base fee for that block will panic in `NextMagmaBlockBaseFee` (division by zero) whenever the block's gas usage is nonzero (the near-universal case). This is a chain-halting bug reachable by a single governance vote from a legitimately-permissioned governance actor (the governing node in `single` mode, which is Mainnet/Kairos's configuration) — no external attacker collusion or malicious validator/consensus-message manipulation is required beyond that one already-authorized vote. This is a Medium-to-High severity finding: it causes complete state-transition failure network-wide (all honest nodes crash identically), effectively a permanent chain halt until manual intervention/patch, which is a much more severe consequence than the original "fairness/UX" impact described in the source report, but it is the same underlying root cause class (unchecked owner/governing-node-set critical parameter).

### Likelihood Explanation
Likelihood is limited by the fact that only the governing node (single mode) or council members (none mode) can cast such a vote — this is a privileged party, not an arbitrary unprivileged transaction sender. However, per the analog rules, "governance parameters" set through this authorized channel are an explicitly in-scope analog category (analogous to the original "onlyOwner critical parameter" pattern). Given the trivial way to trigger it (a single vote value of `0`) and the catastrophic effect (chain-wide panic), even a benign misconfiguration/fat-finger by the governing node (not just malicious intent) would trigger it, unlike input validation bugs whose worst case is subtle unfairness.

### Recommendation
Add an explicit `FormatChecker` for `Kip71GasTarget` (and audit `Kip71BaseFeeDenominator`/`Kip71MaxBlockGasUsedForBaseFee` similarly) that rejects `0` and any other unsafe values, mirroring the relational check already present for `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` in `checkConsistency`. Additionally, add a defensive zero-check/fallback for `gasTarget` inside `NextMagmaBlockBaseFee` (as already done for `BaseFeeDenominator`) so that even a value that slips past governance validation cannot crash the state-transition function.

### Proof of Concept
1. Governing node calls `governance_vote("kip71.gastarget", 0)`. This passes `NewVoteData`'s canonicalization/format-check (`noopFormatChecker` returns `true` for `0`) — `kaiax/gov/headergov/vote.go:29-55`, `kaiax/gov/param.go:324-334`.
2. The vote passes `checkConsistency` because `Kip71GasTarget` has no value-range check — `kaiax/gov/headergov/impl/header.go:213-220`.
3. The vote is written to `header.Vote` and, at the epoch boundary, ratified into `header.Governance`, becoming effective for the next epoch's blocks.
4. Starting from the next epoch, all nodes call `KIP71Config.NextMagmaBlockBaseFee` with `GasTarget = 0` while processing/verifying blocks with nonzero gas usage. The computation `x.Div(x, new(big.Int).SetUint64(0))` in `params/kip71_config.go:100-103` panics, crashing every node that reaches this code path — a full network halt.

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
```

**File:** kaiax/gov/param.go (L324-334)
```go
	Kip71GasTarget: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.GasTarget, nil
		},
		DefaultValue: uint64(30000000),
	},
```

**File:** kaiax/gov/headergov/impl/header.go (L188-201)
```go
	case gov.Kip71LowerBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) > params.UpperBoundBaseFee {
			return ErrLowerBoundBaseFee
		} else {
			return nil
		}
	case gov.Kip71UpperBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) < params.LowerBoundBaseFee {
			return ErrUpperBoundBaseFee
		} else {
			return nil
		}
```

**File:** kaiax/gov/headergov/impl/header.go (L213-220)
```go
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
```

**File:** params/kip71_config.go (L70-103)
```go
	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee

	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}

	// upper gas limit cut off the impulse of used gas to upper bound
	parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)
	if parentGasUsed == gasTarget {
		return makeEvenByFloor(parentBaseFee)
	} else if parentGasUsed > gasTarget {
		// shortcut. If parentBaseFee is already reached upperbound, do not calculate.
		if parentBaseFee.Cmp(upperBoundBaseFee) == 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)
```
