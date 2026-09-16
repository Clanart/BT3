## Title
Missing sanity check on `kip71.gastarget` governance parameter allows a division-by-zero panic in KIP-71 base fee calculation - (File: `params/kip71_config.go`)

### Summary
The `Kip71GasTarget` governance parameter is registered with a `noopFormatChecker` (no bounds/sanity validation) in `kaiax/gov/param.go`, unlike its sibling parameter `Kip71BaseFeeDenominator`, which explicitly rejects zero. `GasTarget` is used as a divisor in `KIP71Config.NextMagmaBlockBaseFee` (`params/kip71_config.go`) without a zero-guard, so a governance vote (or ContractGov update) that sets `kip71.gastarget = 0` causes every node to panic with a division-by-zero when computing/verifying the base fee of the next block whose gas usage differs from the target.

### Finding Description
In `kaiax/gov/param.go`, all KIP-71 parameters except `Kip71BaseFeeDenominator` use `noopFormatChecker`, which unconditionally returns `true`: [1](#0-0) 

Notice that `Kip71BaseFeeDenominator`'s `FormatChecker` explicitly rejects `0` (`return ok && v != 0`), while `Kip71GasTarget` uses `noopFormatChecker` with no such protection.

`headergov`'s `checkConsistency` (called from `VerifyVote`) only cross-checks `LowerBoundBaseFee`/`UpperBoundBaseFee` against each other; `Kip71GasTarget` falls into the default "no additional checks" bucket: [2](#0-1) 

The resulting `GasTarget` value flows unchecked into `KIP71Config.NextMagmaBlockBaseFee`, which is called both when a block is mined and when every node verifies the header's base fee via `VerifyMagmaHeader`: [3](#0-2) 

Note the explicit defensive guard for `BaseFeeDenominator == 0` ("To avoid panic, set the fluctuation range small") right before the code that divides by `GasTarget` with no equivalent guard: [4](#0-3) 

If `GasTarget == 0` and the parent block's gas usage is nonzero (`parentGasUsed > gasTarget`, the "increase" branch, which is the overwhelmingly common case on any active chain), the code performs `x.Div(x, new(big.Int).SetUint64(gasTarget))`, i.e., division by zero: [5](#0-4) 

`big.Int.Div` panics on a zero divisor, so this is reached in the block-assembly path (proposer building the next block) and in the block-verification path (every node validating `header.BaseFee` via `VerifyMagmaHeader`), causing all nodes to panic simultaneously on the same deterministic input — a chain-wide halt.

### Impact Explanation
Once `kip71.gastarget` is set to `0` (via a governance vote in header-governance "single"/"ballot" mode, or via the `GovParam` contract in ContractGov mode — no Solidity-side validation was found either), the very next block whose gas usage is not exactly `0` triggers a deterministic panic in `NextMagmaBlockBaseFee` on every node that assembles or verifies that block. This is a network-wide liveness failure/consensus halt, matching the "acceptance of an invalid ... block" / "state divergence" impact class the rules require, and is strictly more severe than the referenced report (which only concerns UX/fee-fairness in a single contract) because it can halt the entire chain.

### Likelihood Explanation
The parameter is set through the standard, in-scope governance-vote mechanism (a Kaia transaction type/header vote or ContractGov call). No code path — Go-side `FormatChecker`, `checkConsistency`, or (as far as located) Solidity `GovParam` contract — rejects `0` for `kip71.gastarget`, whereas the analogous `BaseFeeDenominator` parameter is explicitly protected. This asymmetry strongly suggests the missing check is an oversight rather than an intentional design choice, making the bug straightforward to trigger by any governance-vote-capable participant.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (and ideally `Kip71MaxBlockGasUsedForBaseFee`, which also feeds `upperGasLimit`) that rejects `0`, mirroring the existing check on `Kip71BaseFeeDenominator`. Additionally, add a defensive zero-check inside `NextMagmaBlockBaseFee` for `gasTarget` (as is already done for `BaseFeeDenominator`) so that a misconfigured or already-committed `0` value cannot crash nodes, and enforce the same non-zero constraint in any Solidity `GovParam` setter path.

### Proof of Concept
1. Have the governing node (single-mode) or a `GovParam`-authorized voter cast a governance vote setting `kip71.gastarget` to `0` — this passes `Params[Kip71GasTarget].FormatChecker` (`noopFormatChecker` always returns `true`) and `checkConsistency` (no case for `Kip71GasTarget` beyond the pass-through default), so the vote is accepted and takes effect at the next epoch, per `kaiax/gov/param.go` lines 324-333 and `kaiax/gov/headergov/impl/header.go` lines 214-220.
2. Once effective, `GetParamSet` propagates `GasTarget = 0` into the chain's `KIP71Config` via `ToKip71Config()` (`kaiax/gov/paramset.go` lines 199-207).
3. On the next block where `parentHeaderGasUsed != 0` (virtually guaranteed on a live network), the proposer calls `NextMagmaBlockBaseFee`, which executes `parentGasUsed > gasTarget` (true, since `gasTarget=0`), reaching `x.Div(x, new(big.Int).SetUint64(0))` at `params/kip71_config.go` line 102, causing a runtime panic.
4. Every other node independently calls `VerifyMagmaHeader` → `NextMagmaBlockBaseFee` while validating that same block, hitting the identical panic — halting the entire network.

### Citations

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

**File:** params/kip71_config.go (L58-90)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}

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
```

**File:** params/kip71_config.go (L92-103)
```go
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
