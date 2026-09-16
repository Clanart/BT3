## #Vulnerability found for this question

### Title
Ungaurded `kip71.gastarget` governance parameter can be voted to zero, causing a division-by-zero panic in `NextMagmaBlockBaseFee` that halts block header validation for every subsequent transaction - ([File: params/kip71_config.go])

### Summary
The reported Olympus SSLV bug is a "new internal state added without validating an edge-case value" bug class: an admin sets a token's `_startTimeStamp` in the future, and every unprivileged deposit/withdraw thereafter reverts because `_accumulateInternalRewards` performs `block.timestamp - lastRewardTime` unconditionally. The analogous root cause exists in Kaia's KIP-71 dynamic base-fee module: the governance parameter `kip71.gastarget` (`gov.Kip71GasTarget`) can be updated to `0` through the normal governance vote mechanism without any format or consistency validation, and the base-fee calculation in `NextMagmaBlockBaseFee` unconditionally divides by `gasTarget`, causing a division-by-zero panic that is triggered by ordinary blocks (i.e., any transaction inclusion), blocking transaction processing network-wide.

### Finding Description
`Kip71GasTarget` is registered with `FormatChecker: noopFormatChecker`, meaning any `uint64` value—including `0`—passes validation when a vote is submitted: [1](#0-0) 

Unlike `Kip71BaseFeeDenominator`, which explicitly rejects `0` (`return ok && v != 0`), `GasTarget` has no such guard: [2](#0-1) 

The header-governance consistency checker (`checkConsistency`) also does not perform any extra validation for `gov.Kip71GasTarget`; it falls into the generic "format-checked only" branch: [3](#0-2) 

Once `GasTarget = 0` becomes effective, `NextMagmaBlockBaseFee` is invoked on every block header validation (via `VerifyMagmaHeader`) and on every new block assembly. When the parent block used more gas than the (now zero) target, execution unconditionally divides by `gasTarget`: [4](#0-3) 

Since `gasTarget` is `0` and `parentGasUsed > 0` (essentially every non-empty block), `x.Div(x, new(big.Int).SetUint64(0))` triggers a `math/big` division-by-zero panic. This function is called from block header validation: [5](#0-4) 

Because block validation (and correspondingly block production, since the proposer computes the same `BaseFee` to populate the header) invokes this code path for **every** block containing any gas usage, a panic here effectively halts header validation/assembly network-wide — analogous to the SSLV bug where every deposit/withdraw call reverted due to an unguarded per-call computation triggered by a single bad configuration value.

### Impact Explanation
This is a governance-parameter-triggered denial-of-service: once `kip71.gastarget` is set to `0` (a value the format checker does not reject), every subsequent block with non-zero gas usage causes a panic in `NextMagmaBlockBaseFee`, crashing/rejecting header validation both for consensus nodes producing blocks and full/light nodes verifying them. This blocks all transaction inclusion and processing chain-wide, matching the "state transition and gas/burn accounting" / "KIP-71 pricing" category, and results in acceptance-of-invalid-transaction/block class disruption (chain halt) rather than a mere isolated revert.

### Likelihood Explanation
The parameter is exposed through the standard header-governance vote mechanism (`gov.Kip71GasTarget`), and no format or consistency check prevents `0` from being accepted, unlike sibling parameters (`Kip71BaseFeeDenominator`) that are explicitly hardened against this exact class of misconfiguration. Any governance vote flow (single-governance mode or contract-governance mode) that sets `GasTarget = 0` will unconditionally trigger the panic on the very next non-empty block, making the likelihood of triggering it — once such a vote is cast — deterministic and immediate.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` that rejects `0`, mirroring `Kip71BaseFeeDenominator`'s guard (`return ok && v != 0`). Additionally, add a defensive check inside `NextMagmaBlockBaseFee` to treat `gasTarget == 0` the same way `BaseFeeDenominator == 0` is already handled (fallback to a safe default) instead of dividing directly by the governance-supplied value.

### Proof of Concept
1. Submit a governance vote setting `kip71.gastarget = 0` via the standard header-vote mechanism; `NewVoteData`/`FormatChecker`/`checkConsistency` all accept it ( [1](#0-0) , [3](#0-2) ).
2. Once the vote takes effect at the target block, any subsequent block whose parent used gas > 0 (virtually any block after Magma fork) causes `NextMagmaBlockBaseFee` to execute the `parentGasUsed > gasTarget` branch with `gasTarget = 0`: [6](#0-5) 
3. `y := x.Div(x, new(big.Int).SetUint64(0))` panics (Go `math/big` division by zero).
4. This is invoked from `BlockValidator.validateHeader` for every header after the fork ( [5](#0-4) ) and from block-assembly code computing the next `BaseFee`, so all nodes panic on validating/producing the next non-empty block, halting the chain and blocking all transaction processing.

### Citations

**File:** kaiax/gov/param.go (L310-323)
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

**File:** blockchain/block_validator.go (L202-213)
```go
	// Verify Magma basefee rule from governance paramset.
	if v.config.IsMagmaForkEnabled(header.Number) {
		// Skip governance-dependent validation when gov module is not registered.
		if v.mGov != nil {
			govParamSet := v.mGov.GetParamSet(header.Number.Uint64())
			if err := govParamSet.ToKip71Config().VerifyMagmaHeader(header.BaseFee, parent.Number, parent.BaseFee, parent.GasUsed); err != nil {
				return err
			}
		}
	} else if header.BaseFee != nil {
		return ErrInvalidBaseFee
	}
```
