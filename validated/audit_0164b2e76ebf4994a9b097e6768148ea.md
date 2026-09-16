### Title
Missing zero-validation on `governance.kip71.gastarget` causes division-by-zero panic in KIP-71 base fee calculation - (File: `params/kip71_config.go`)

### Summary
The `kip71.gastarget` governance parameter has no format check preventing a zero value (`Kip71GasTarget` uses `noopFormatChecker`), and `checkConsistency()` in the header-governance vote-verification path also performs no non-zero check for this parameter. Once a vote setting `gastarget=0` is ratified, every node's `KIP71Config.NextMagmaBlockBaseFee()` divides by `gasTarget` while computing the next block's base fee, causing a `division by zero` panic in `math/big` on essentially every subsequent block.

### Finding Description
`params.KIP71Config.NextMagmaBlockBaseFee()` uses `kc.GasTarget` as a divisor without any zero-guard, unlike `BaseFeeDenominator`, which explicitly special-cases the zero value: [1](#0-0) 

When the parent's gas usage differs from `gasTarget`, the delta is divided by `gasTarget`: [2](#0-1) 

If `gasTarget == 0` and `parentGasUsed > 0` (true for virtually every non-empty block), execution enters the `parentGasUsed > gasTarget` branch and calls `x.Div(x, new(big.Int).SetUint64(gasTarget))` with a zero divisor, which panics in Go's `math/big` package (this mirrors the TensorFlow `InplaceSub` bug class: a divisor that should be validated as non-zero is instead used unconditionally).

The parameter is defined with no format validation: [3](#0-2) 

And the header-governance vote consistency check treats `Kip71GasTarget` as a param requiring no additional runtime check beyond `NewVoteData()`'s format checker (which is a no-op for this parameter): [4](#0-3) 

Only `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` receive additional cross-field validation in `checkConsistency`; `Kip71GasTarget` is not one of them: [5](#0-4) 

The vote is exposed via the public/documented `governance_vote` JSON-RPC API: [6](#0-5) 

Once the vote is cast by the governing node (in `single` governance mode, which is the mode used on Mainnet/Kairos) and ratified at an epoch boundary, `GetParamSet()` propagates `GasTarget=0` into `ChainConfig.Governance.KIP71`, which is then used unconditionally by every node computing/verifying `header.BaseFee` for blocks in the new epoch (via `blockchain/tx_pool.go`, `blockchain/chain_makers.go`, `work/worker.go`, and `node/cn/gasprice/*`), since these all call `NextMagmaBlockBaseFee`.

### Impact Explanation
This is a network-wide denial-of-service / consensus-halting bug, not merely an individual-node crash: because `NextMagmaBlockBaseFee()` is invoked by every Magma-enabled node both when constructing (`work/worker.go`) and validating (`blockchain/tx_pool.go`, `blockchain/chain_makers.go`) a header's `BaseFee`, all conforming nodes will hit the same division-by-zero panic on the first non-empty block after the malicious `gastarget=0` change takes effect. This halts block production/validation network-wide until manually patched/restarted, which is a severe availability impact satisfying the "state divergence between honest nodes / acceptance of an invalid transaction or block" criteria (in this case, complete inability to process any block, a stronger outcome).

### Likelihood Explanation
Exploitation requires only a single `governance_vote` JSON-RPC call from the governing node (in `single` mode, the standard configuration) with `("governance.kip71.gastarget", 0)`. No consensus/BFT-message forgery, key leakage, or peer/p2p exploitation is required — this is a legitimate, permitted governance parameter within the current validation framework, and the format checker and the consistency checker both fail to reject the zero value that leads directly to a crash. The vote passes all currently implemented format and consistency checks, as shown by the same `checkConsistency` code path that explicitly whitelists `Kip71GasTarget` under "no more checks here."

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` (analogous to `Kip71BaseFeeDenominator`, whose checker already requires `v != 0`) in `kaiax/gov/param.go`, and add a corresponding zero-guard fallback in `KIP71Config.NextMagmaBlockBaseFee()` (`params/kip71_config.go`) mirroring the existing `BaseFeeDenominator == 0` fallback, so that `gasTarget == 0` cannot reach the division operations.

### Proof of Concept
1. Deploy/operate a `single`-mode governance chain where the attacker controls the `governance.governingnode` account (the standard Mainnet/Kairos configuration).
2. Call the public RPC:
```
curl "http://localhost:8551" -X POST -H 'Content-Type: application/json' --data '
  {"jsonrpc":"2.0","id":1,"method":"governance_vote","params":[
    "governance.kip71.gastarget",
    0
  ]}'
```
This passes `NewVoteData`'s `noopFormatChecker` for `Kip71GasTarget` [3](#0-2)  and `checkConsistency`'s pass-through branch [4](#0-3) .
3. Once the proposer includes this vote and it is ratified at the epoch boundary, `GasTarget=0` becomes the effective governance parameter for the next epoch.
4. On the next block with any nonzero `GasUsed` (virtually guaranteed), `NextMagmaBlockBaseFee` executes:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // = parentGasUsed
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget)) // Div(x, 0) → panic
``` [7](#0-6) 
causing every node computing/verifying this header's `BaseFee` to panic and halt.

### Citations

**File:** params/kip71_config.go (L70-78)
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
```

**File:** params/kip71_config.go (L96-121)
```go
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)

		nextBaseFee := x.Add(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(upperBoundBaseFee) > 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	} else {
		// shortcut. If parentBaseFee is already reached lower bound, do not calculate.
		if parentBaseFee.Cmp(lowerBoundBaseFee) == 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)
```

**File:** kaiax/gov/param.go (L324-333)
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
