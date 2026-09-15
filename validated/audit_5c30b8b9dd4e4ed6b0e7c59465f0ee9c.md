### Title
Governance-Set `kip71.gastarget` of Zero Causes Division-by-Zero Panic in `NextMagmaBlockBaseFee`, Halting the Entire Network - (File: `params/kip71_config.go`)

### Summary
The `kip71.gastarget` governance parameter can be set to `0` through both header governance voting and the on-chain `GovParam` contract, because its `FormatChecker` performs no validation. A `GasTarget` of `0` causes `NextMagmaBlockBaseFee()` to execute a `big.Int` division by zero, which panics in Go. This function is invoked on every block during header verification and block production, so setting this single parameter crashes every honest node, producing a permanent, network-wide Denial-of-Service — directly analogous to the reported `changeInterval` overflow bug where an unrestricted, governance-controlled numeric parameter feeds into unguarded arithmetic reachable on every "upkeep" (here, every block).

### Finding Description
`kip71.gastarget` is declared with a `noopFormatChecker`, meaning any `uint64` value, including `0`, is accepted as valid: [1](#0-0) 

Both governance channels can push this unchecked value:
- Header governance vote validation (`checkConsistency`) treats `Kip71GasTarget` as one of the "no more checks here" parameters, only relying on `NewVoteData`'s format check, which is the no-op checker: [2](#0-1) 
- Contract governance (`GovParam.setParam`/`setParamIn`, restricted to the contract owner/GC) stores the value as raw bytes and `contractGovModule.GetParamSet` applies the same `Set()`/format-check pipeline, accepting `0` for `GasTarget` the same way: [3](#0-2) 

Once `GasTarget = 0` is active, `NextMagmaBlockBaseFee()` computes the base fee for the next block. If `parentGasUsed != 0` (virtually always true), the code takes the "gas used above target" branch and divides by `gasTarget`: [4](#0-3) 

`new(big.Int).SetUint64(gasTarget)` is `0`, so `x.Div(x, 0)` panics with "division by zero" (Go's `math/big.Int.Div` panics on a zero divisor, unlike the `BaseFeeDenominator == 0` case a few lines above which is explicitly special-cased to avoid exactly this panic).

Note that `BaseFeeDenominator` has an explicit zero-guard (`if kc.BaseFeeDenominator == 0 { baseFeeDenominator = 64 }`), but `GasTarget` has no equivalent guard — an inconsistency that indicates the omission is a genuine oversight rather than an intentional design decision.

### Impact Explanation
`NextMagmaBlockBaseFee` (and its wrapper `VerifyMagmaHeader`) is called from core consensus and mempool paths on every block: [5](#0-4) 
- Block header verification (`blockchain/block_validator.go`)
- Block production (`work/worker.go`)
- Transaction pool base-fee tracking (`blockchain/tx_pool.go`)
- Gas price oracle / fee history RPC (`node/cn/gasprice/gasprice.go`, `node/cn/gasprice/feehistory.go`)

A panic in any of these paths crashes the node process. Since every honest node (validators and full nodes alike) runs this same code deterministically on the same chain state, this is a network-wide chain halt, not merely a single-node fault — a Critical-severity liveness failure of the entire chain until governance is manually rolled back (which itself requires the chain to be running).

### Likelihood Explanation
The condition can be triggered by a single governance action from a GC member/governing node (contract owner of `GovParam`, or the vote-casting proposer in `single` governance mode): submitting a vote or contract call setting `kip71.gastarget` to `0`. No other consistency guard (`checkConsistency`) rejects this value, unlike `LowerBoundBaseFee`/`UpperBoundBaseFee`, which do have cross-field consistency checks. This mirrors the original report's scenario where a privileged-but-not-fully-trusted role (fee controller / GC member) can set an unconstrained governance-style parameter that feeds unguarded arithmetic reachable on every subsequent state transition.

### Recommendation
Add an explicit `FormatChecker` for `Kip71GasTarget` (and ideally `Kip71MaxBlockGasUsedForBaseFee`) rejecting `0`, mirroring the existing zero-guard already present for `BaseFeeDenominator`. Additionally, add a defensive zero-check directly in `NextMagmaBlockBaseFee()` before dividing by `gasTarget`, so that even historical or externally-injected configurations cannot trigger a panic.

### Proof of Concept
1. As the governing node (or GC member with `single`/`ballot` voting rights), cast a header vote or `GovParam.setParamIn("kip71.gastarget", true, <8 zero bytes>, 1)` contract call.
2. Once the parameter activates at the next epoch/effective block, any block whose `GasUsed != 0` triggers:
   - `parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)` → nonzero
   - `parentGasUsed > gasTarget (0)` branch taken
   - `y := x.Div(x, new(big.Int).SetUint64(0))` → Go runtime panic "division by zero"
3. This panic occurs in `VerifyMagmaHeader`/`NextMagmaBlockBaseFee`, called from `block_validator.go`, `worker.go`, and `tx_pool.go`, crashing every node processing or producing that block, halting the chain.

### Citations

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

**File:** kaiax/gov/contractgov/impl/getter.go (L17-31)
```go
func (c *contractGovModule) GetParamSet(blockNum uint64) gov.ParamSet {
	m, err := c.contractGetAllParamsAt(blockNum)
	if err != nil {
		return *gov.GetDefaultGovernanceParamSet()
	}

	ret := *gov.GetDefaultGovernanceParamSet()
	for k, v := range m {
		err = ret.Set(k, v)
		if err != nil {
			return *gov.GetDefaultGovernanceParamSet()
		}
	}

	return ret
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

**File:** blockchain/block_validator.go (L1-1)
```go
// Modifications Copyright 2024 The Kaia Authors
```
