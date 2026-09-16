### Title
Missing bounds validation on KIP-71 `GasTarget` (and other base-fee) governance parameters causes division-by-zero panic in base fee computation - ([File: kaiax/gov/param.go], [File: params/kip71_config.go])

### Summary
The `Curve2TokenConvexVault.initialize` bug pattern — accepting arbitrary settings without validating that they fall within safe operational bounds, leading to overflow/underflow when the value is later used in arithmetic — has a direct analog in Kaia's governance parameter framework. The `kip71.gastarget` parameter (along with `kip71.lowerboundbasefee`, `kip71.upperboundbasefee`, and `kip71.maxblockgasusedforbasefee`) is registered with a `noopFormatChecker`, meaning no bounds/non-zero check is performed when the value is voted in through governance, unlike the sibling parameter `kip71.basefeedenominator` which explicitly rejects zero. [1](#0-0) 

### Finding Description
`Kip71GasTarget`'s `Param` definition uses `FormatChecker: noopFormatChecker`, which unconditionally returns `true` for any `uint64` value, including `0`. [2](#0-1) [3](#0-2) 

By contrast, `Kip71BaseFeeDenominator` explicitly guards against zero (`return ok && v != 0`), showing the maintainers were aware that a zero divisor is unsafe for this family of parameters, but the equivalent check was omitted for `GasTarget`. [4](#0-3) 

This value flows unchecked through `PartialParamSet.Add` (canonicalize + format-check) and `ParamSet.Set` into the effective `gov.ParamSet.GasTarget`, and ultimately into `params.KIP71Config.GasTarget` via `ToKip71Config()`. [5](#0-4) [6](#0-5) 

`NextMagmaBlockBaseFee` (called both by `VerifyMagmaHeader` during header verification and by block-assembly code) divides by `gasTarget` without checking for zero:
```
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // divide-by-zero panic if gasTarget == 0
``` [7](#0-6) 

If `GasTarget` is ratified to `0` and any subsequent block has non-zero gas usage (`parentGasUsed > gasTarget`), `big.Int.Div` will panic on every node that verifies or builds that block, because `checkConsistency` in `headergov` only validates `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` against each other, and passes `Kip71GasTarget` and `Kip71MaxBlockGasUsedForBaseFee` through untouched. [8](#0-7) 

### Impact Explanation
This is a governance-parameter validation gap (an explicitly in-scope category). A single malformed/malicious vote for `kip71.gastarget = 0`, once ratified at an epoch boundary, becomes the effective parameter for the entire next epoch. Every node (proposers building blocks and validators verifying headers) that executes `NextMagmaBlockBaseFee`/`VerifyMagmaHeader` for that period will panic deterministically on the first block with nonzero gas usage, since base-fee computation is mandatory consensus logic embedded in header preparation and verification. This results in a chain-wide halt (all conforming nodes crash simultaneously), which is a severe liveness/availability failure of the base fee mechanism defined by KIP-71.

### Likelihood Explanation
Reaching this state requires a governance vote for `kip71.gastarget` to be cast and ratified. The `governance_vote` RPC path enforces that the voter must be a Governance Council member (and in `single` mode, specifically the governing node) as checked in `VerifyVote`/`checkConsistency`. [9](#0-8) 
While this constrains who can trigger it to a governance actor rather than a fully unprivileged sender, no additional safety net (bounds check, non-zero enforcement) exists anywhere in the pipeline — a single vote by one authorized entity (or a misconfigured/compromised governing node) is sufficient to crash the network, with no code path catching or rejecting the unsafe value before it reaches the consensus-critical arithmetic.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects zero (mirroring `Kip71BaseFeeDenominator`), e.g.:
```go
Kip71GasTarget: {
    Canonicalizer: uint64Canonicalizer,
    FormatChecker: func(cv any) bool {
        v, ok := cv.(uint64)
        return ok && v != 0
    },
    ...
}
```
Additionally, defensively guard `NextMagmaBlockBaseFee` against `gasTarget == 0` (similar to the existing `BaseFeeDenominator == 0` fallback) so that a bad historical/legacy value cannot crash node operation, and consider adding cross-field consistency checks (e.g., `MaxBlockGasUsedForBaseFee >= GasTarget`) in `checkConsistency`.

### Proof of Concept
1. Governing node (or GC member in `none` mode) calls `governance_vote("kip71.gastarget", 0)`. [10](#0-9) 
2. The vote passes `NewVoteData` (no format check rejects `0`) and `checkConsistency` (no case handles `Kip71GasTarget` specially, falls through to the "no more checks" default `return nil` case). [11](#0-10) 
3. At the next epoch boundary the vote is ratified into `header.Governance`, becoming effective for the following epoch.
4. Any block in that epoch with `parentGasUsed > 0` triggers `x.Div(x, new(big.Int).SetUint64(0))` inside `NextMagmaBlockBaseFee`, panicking in both the block-producing node (`PrepareHeader`) and every verifying node (`VerifyMagmaHeader`), halting the chain. [12](#0-11)

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
```

**File:** kaiax/gov/param.go (L310-334)
```go
	Kip71BaseFeeDenominator: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: func(cv any) bool {
			v, ok := cv.(uint64)
			return ok && v != 0
		},
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.BaseFeeDenominator, nil
		},
		DefaultValue: uint64(20),
	},
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

**File:** kaiax/gov/paramset.go (L199-207)
```go
func (p *ParamSet) ToKip71Config() *params.KIP71Config {
	return &params.KIP71Config{
		LowerBoundBaseFee:         p.LowerBoundBaseFee,
		UpperBoundBaseFee:         p.UpperBoundBaseFee,
		GasTarget:                 p.GasTarget,
		MaxBlockGasUsedForBaseFee: p.MaxBlockGasUsedForBaseFee,
		BaseFeeDenominator:        p.BaseFeeDenominator,
	}
}
```

**File:** kaiax/gov/paramset.go (L209-226)
```go
func (p PartialParamSet) Add(name string, value any) error {
	param, ok := Params[ParamName(name)]
	if !ok {
		return ErrInvalidParamName
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		return err
	}

	if !param.FormatChecker(cv) {
		return ErrInvalidParamValue
	}

	p[ParamName(name)] = cv
	return nil
}
```

**File:** params/kip71_config.go (L88-103)
```go
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

**File:** kaiax/gov/headergov/impl/header.go (L61-109)
```go
func (h *headerGovModule) VerifyVote(header *types.Header) error {
	if len(header.Vote) == 0 {
		return nil
	}

	var (
		vb       headergov.VoteBytes = header.Vote
		blockNum                     = header.Number.Uint64()
	)

	vote, err := vb.ToVoteData()
	if err != nil {
		logger.Error("ToVoteData error", "num", blockNum, "vote", vb, "err", err)
		return err
	}

	if gov.DeprecatedAt(vote.Name(), h.ChainConfig.Rules(header.Number)) {
		logger.Error("Vote is deprecated", "num", blockNum, "name", vote.Name())
		return ErrDeprecatedVote
	}

	council, err := h.ValSet.GetCouncil(blockNum)
	if err != nil {
		return err
	}

	// check if the voter is in council
	if !slices.Contains(council, vote.Voter()) {
		return ErrInvalidKeyValue
	}

	// check if Voter is the block proposer.
	author, err := h.Chain.Sealer().Author(header)
	if err != nil {
		return err
	}
	if author != vote.Voter() {
		return ErrInvalidVoter
	}

	// In single mode, only the governing node can write header.Vote after Permissionless.
	params := h.GetParamSet(blockNum)
	if h.ChainConfig.IsPermissionlessForkEnabled(new(big.Int).SetUint64(blockNum)) &&
		params.GovernanceMode == "single" &&
		vote.Voter() != params.GoverningNode {
		return ErrVotePermissionDenied
	}

	return h.checkConsistency(blockNum, vote)
```

**File:** kaiax/gov/headergov/impl/header.go (L188-220)
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
	case gov.AddValidator, gov.RemoveValidator:
		params := h.GetParamSet(blockNum)

		// compare with governing node only in single mode.
		if params.GovernanceMode != "single" {
			return nil
		}
		if slices.Contains(vote.Value().([]common.Address), params.GoverningNode) {
			return ErrGovNodeInValSetVoteValue
		}
		return nil
		// These votes are valid as long as it passes the format checks in NewVoteData(). No more checks here.
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
```

**File:** kaiax/gov/headergov/impl/api.go (L53-83)
```go
func (api *headerGovAPI) Vote(name string, value any) (string, error) {
	var (
		voter     = api.h.nodeAddress
		nextBlock = api.h.Chain.CurrentBlock().NumberU64() + 1
		gp        = api.h.GetParamSet(nextBlock)
		gMode     = gp.GovernanceMode
	)

	if gMode == "single" && voter != gp.GoverningNode {
		return "", ErrVotePermissionDenied
	}

	vote := headergov.NewVoteData(voter, name, value)
	if vote == nil {
		return "", ErrInvalidKeyValue
	}

	if gov.DeprecatedAt(vote.Name(), api.h.ChainConfig.Rules(new(big.Int).SetUint64(nextBlock))) {
		return "", ErrDeprecatedVote
	}

	err := api.h.checkConsistency(nextBlock, vote)
	if err != nil {
		return "", err
	}

	// TODO-kaiax: add removevalidator vote check

	api.h.PushMyVotes(vote)
	return "(kaiax) Your vote is prepared. It will be put into the block header or applied when your node generates a block as a proposer. Note that your vote may be duplicate.", nil
}
```
