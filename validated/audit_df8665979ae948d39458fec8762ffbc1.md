### Title
Missing zero-value validation for `Kip71GasTarget` governance parameter leads to divide-by-zero panic in KIP-71 base fee calculation - ([File: params/kip71_config.go])

### Summary
The `kip71.gastarget` governance parameter can be voted to `0` because its `FormatChecker` is a no-op, while the sibling parameter `Kip71BaseFeeDenominator` explicitly rejects `0`. Once `GasTarget == 0` is active, `KIP71Config.NextMagmaBlockBaseFee` divides by `gasTarget` without a zero-check, causing a `big.Int` "division by zero" panic on every node that computes the next Magma/KIP-71 base fee (block validation, block building, and `eth_feeHistory`/gas price RPCs).

### Finding Description
`Kip71GasTarget` is registered with `noopFormatChecker`, which always returns `true`, unlike `Kip71BaseFeeDenominator` which explicitly requires `v != 0`: [1](#0-0) 

`checkConsistency`, which validates governance votes embedded in block headers, has no special-case validation for `gov.Kip71GasTarget` either — it falls into the generic "format-check-only" branch that returns `nil`: [2](#0-1) 

This means a governance vote setting `kip71.gastarget = 0` (cast by the governing node, or under general/permissionless governance modes by any voting validator) passes both `FormatChecker` and `checkConsistency`, gets embedded into the epoch's `Governance` field, and becomes the active `GasTarget` for subsequent blocks via `ParamSet`/`ToKip71Config()`: [3](#0-2) 

Once active, `KIP71Config.NextMagmaBlockBaseFee` uses `gasTarget` as an unchecked divisor in both branches (gas used above target, and below target): [4](#0-3) 

Specifically:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))   // gasTarget == 0 -> panic
```
Go's `math/big.Int.Div`/`QuoRem` panics with "division by zero" when the divisor is zero. Since virtually every block has `parentGasUsed > 0`, the `parentGasUsed == gasTarget` (== 0) short-circuit branch is not taken, and the panic path is hit on essentially every subsequent block.

This function is reachable from multiple unauthenticated/public-facing code paths that any consensus-participating node executes:
- Block header validation, called for every incoming block: `blockchain/block_validator.go` (`VerifyMagmaHeader`/`NextMagmaBlockBaseFee`).
- Block/base-fee assembly during mining: `work/worker.go`.
- Transaction pool gas-price gating: `blockchain/tx_pool.go`.
- Public JSON-RPC `eth_feeHistory` handler: `node/cn/gasprice/feehistory.go` (`processBlock`), directly reachable by any public RPC caller.
- Gas price oracle used by `eth_gasPrice`/`eth_maxPriorityFeePerGas`: `node/cn/gasprice/gasprice.go`.

### Impact Explanation
A panic inside block validation or block production (`blockchain/block_validator.go`, `work/worker.go`) crashes the node process on every honest node that processes a block after the malicious `GasTarget=0` governance change takes effect at the next epoch boundary. Because this logic is deterministic and identical on all nodes, the panic occurs network-wide simultaneously, resulting in a full chain halt (denial of service across the whole network) rather than a single-node crash. Additionally, any public RPC node serving `eth_feeHistory` calls can be crashed on-demand by any unprivileged RPC caller once the malicious parameter is active, since `processBlock` computes `NextMagmaBlockBaseFee` unconditionally for the next block when Magma is active. This satisfies "acceptance of an invalid transaction or block" / state divergence and chain-halting criteria via a Medium-severity governance-parameter validation gap analogous to the WavPack CWE-369 divide-by-zero.

### Likelihood Explanation
Setting the malicious value requires a party with vote-casting rights (governing node in "single" governance mode, or any council/validator member under other governance modes) to submit a `kip71.gastarget = 0` vote. This is a normal governance-parameter operation with no special access beyond standard vote-casting rights that the protocol already assumes are usable — the code has an explicit precedent (`BaseFeeDenominator`) showing the developers intended to disallow zero for a directly analogous divisor parameter but omitted the same guard for `GasTarget`. No cryptographic bypass, node compromise, or p2p exploitation is required — only a standard, already-supported governance vote path, making this straightforward to trigger once vote rights are held.

### Recommendation
Add a `FormatChecker` for `Kip71GasTarget` in `kaiax/gov/param.go` that rejects `0` (mirroring `Kip71BaseFeeDenominator`'s `v != 0` check), e.g.:
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
As defense in depth, also add an explicit zero-guard inside `KIP71Config.NextMagmaBlockBaseFee` in `params/kip71_config.go` (similar to the existing `BaseFeeDenominator == 0` fallback) so that a `GasTarget` of `0` degrades gracefully instead of panicking, protecting already-deployed chains where a bad value might already be committed to chain history.

### Proof of Concept
1. Governing node (or any validator with vote rights) casts a header vote: `headergov.NewVoteData(voter, "kip71.gastarget", uint64(0))`. This passes `NewVoteData`/`FormatChecker` (no-op) and `checkConsistency` (default `nil` branch), so `VerifyVote` in `kaiax/gov/headergov/impl/header.go` accepts it.
2. At the next epoch boundary, the vote is finalized into `Governance` and `GasTarget=0` becomes the active `ParamSet.GasTarget`, exposed via `ParamSet.ToKip71Config()`.
3. On the next block after Magma/KIP-71 is active with `parentHeaderGasUsed > 0` (virtually guaranteed), any node calling `KIP71Config.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)` — reached from `blockchain/block_validator.go` header verification, `work/worker.go` block assembly, or the public `eth_feeHistory` RPC handler in `node/cn/gasprice/feehistory.go` — executes:
```go
gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget) // gasTarget=0
x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
y := x.Div(x, new(big.Int).SetUint64(gasTarget))                  // panics: division by zero
```
4. The panic crashes the node process; because every honest node executes identical logic on the same block, the entire network halts simultaneously.

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

**File:** kaiax/gov/headergov/impl/header.go (L214-220)
```go
	case gov.GovernanceDeriveShaImpl, gov.GovernanceGovParamContract, gov.GovernanceGovernanceMode, gov.GovernanceUnitPrice,
		gov.IstanbulCommitteeSize, gov.IstanbulEpoch, gov.IstanbulPolicy,
		gov.Kip71BaseFeeDenominator, gov.Kip71GasTarget, gov.Kip71MaxBlockGasUsedForBaseFee,
		gov.RewardDeferredTxFee, gov.RewardKip82Ratio, gov.RewardMintingAmount, gov.RewardMinimumStake,
		gov.RewardProposerUpdateInterval, gov.RewardRatio, gov.RewardStakingRewardThreshold,
		gov.RewardStakingUpdateInterval, gov.RewardUseFlexReward, gov.RewardUseGiniCoeff:
		return nil
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

**File:** params/kip71_config.go (L88-128)
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

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
```
