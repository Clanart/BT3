I have enough information to produce the analog finding now.

### Title
Missing bound validation on `governance.unitprice` allows setting the network gas price to zero or an arbitrarily extreme value - (File: kaiax/gov/param.go)

### Summary
The Kaia governance parameter registry defines a `FormatChecker` hook for every governance parameter that is meant to reject malformed or out-of-range values before a vote/governance change is accepted. For `GovernanceUnitPrice` — the parameter that fixes the network-wide gas price used pre-Magma and as the tx-pool price floor — this hook is set to `noopFormatChecker`, which unconditionally returns `true` and therefore performs no validation at all, unlike sibling numeric params such as `IstanbulCommitteeSize` (`v > 0`) or `Kip71BaseFeeDenominator` (`v != 0`).

### Finding Description
`gov.Params[GovernanceUnitPrice]` is declared with `FormatChecker: noopFormatChecker` and `Canonicalizer: uint64Canonicalizer`, meaning any `uint64` value — including `0` — passes format validation with no upper- or lower-bound check. [1](#0-0) 

This value flows through `headergov.NewVoteData`, which only calls `Canonicalizer` then `FormatChecker` before accepting the vote as canonical: [2](#0-1) 

`VerifyVote`/`checkConsistency` then explicitly allow `GovernanceUnitPrice` through with no additional cross-parameter or bound checks — contrasted with `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, which *do* get bound-checked against each other in the same switch statement: [3](#0-2) 

`GovernanceUnitPrice` is the value used pre-Magma (and as the tx-pool floor after Magma via `pool.gasPrice`) to gate every transaction's required `GasPrice`/`GasFeeCap`/`GasTipCap`: [4](#0-3) 

Setting it to `0` removes the fee floor entirely (any transaction with `gasPrice == 0` becomes valid pre-Magma), and setting it to `math.MaxUint64` makes every legitimate transaction fail the exact-match check (`ErrInvalidUnitPrice`) or become economically infeasible, effectively halting user transaction submission chain-wide. This is analogous to the reported bug class: a governance-controlled fee/price setter with no bound validation, where a single erroneous or malicious value (whether from a compromised governing node in "single" mode, or a validator/majority-vote in "none" mode) is accepted and propagated as if valid.

### Impact Explanation
An unbounded `governance.unitprice` value directly controls the fee that senders/fee-payers must pay for every transaction on the network. A misconfigured or maliciously-set value of `0` bypasses the network's fee mechanism (loss of expected fee revenue / spam resistance), while an extreme value effectively locks out ordinary transaction senders network-wide (denial of service to all public-RPC callers submitting transactions), since `tx_pool.validateTx` will reject transactions that don't exactly match `pool.gasPrice` pre-Magma. Both outcomes match the reported class of "loss of funds/fee integrity due to unchecked fee parameter," escalated here to a chain-wide DoS/fee-bypass rather than a single dApp's fee.

### Likelihood Explanation
Reaching this requires a governance vote to be accepted — either the sole governing node in "single" mode, or a validator council majority in "none" mode — casting a vote for `governance.unitprice`. This is the standard, intended path for changing this specific parameter (unlike node-operator/private-key-compromise scenarios), and the code's own comment style / analogous checks on `Kip71LowerBoundBaseFee`/`UpperBoundBaseFee` show that the maintainers intended bound-style sanity checks for governance-set fee parameters — the omission for `GovernanceUnitPrice` is a genuine gap in this pattern, matching the "no fee validation" root cause of the reference report (an unbounded admin-settable fee value causing unbounded impact from a single misconfiguration).

### Recommendation
Add a non-trivial `FormatChecker` for `GovernanceUnitPrice` (e.g., reject `0` and cap the maximum to some sane multiple of the current value or a fixed ceiling, similar to `Kip71BaseFeeDenominator`'s `v != 0` check), and/or add a `checkConsistency` case bounding new `unitprice` votes relative to the previous value (as already done for `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`).

### Proof of Concept
1. As the governing node (in `governance.governancemode == "single"`) or via validator majority (in `"none"` mode), submit a vote: `headergov.NewVoteData(voter, "governance.unitprice", uint64(0))`.
2. `NewVoteData` canonicalizes to `uint64(0)` and passes `noopFormatChecker` unconditionally — see [1](#0-0) .
3. `VerifyVote`/`checkConsistency` accept the vote with no bound check — see [5](#0-4) .
4. Once the vote is applied at the next epoch, `pool.gasPrice` becomes `0` (pre-Magma), and `blockchain/tx_pool.go`'s `pool.gasPrice.Cmp(tx.GasPrice()) != 0` check now accepts `gasPrice == 0` transactions — see [6](#0-5) , allowing zero-fee transaction spam chain-wide. Conversely, voting `math.MaxUint64` makes the exact-match check fail for all realistic sender-submitted gas prices, DoS-ing transaction submission network-wide.

### Citations

**File:** kaiax/gov/param.go (L259-264)
```go
	GovernanceUnitPrice: {
		Canonicalizer:    uint64Canonicalizer,
		FormatChecker:    noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) { return c.UnitPrice, nil },
		DefaultValue:     uint64(250e9),
	},
```

**File:** kaiax/gov/headergov/vote.go (L29-55)
```go
func NewVoteData(voter common.Address, name string, value any) VoteData {
	param, ok := gov.Params[gov.ParamName(name)]
	if !ok {
		param, ok = gov.ValidatorParams[gov.ParamName(name)]
		if !ok {
			logger.Error("Invalid vote name", "name", name)
			return nil
		}
	}

	cv, err := param.Canonicalizer(value)
	if err != nil {
		logger.Error("Canonicalize error", "name", name, "value", value, "err", err)
		return nil
	}

	if !param.FormatChecker(cv) {
		logger.Error("Format check error", "name", name, "value", value)
		return nil
	}

	return &voteData{
		voter: voter,
		name:  gov.ParamName(name),
		value: cv,
	}
}
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

**File:** blockchain/tx_pool.go (L868-882)
```go
	} else {
		if pool.rules.IsMagma {
			if pool.gasPrice.Cmp(tx.GasPrice()) > 0 {
				// Ensure transaction's gasPrice is greater than or equal to transaction pool's gasPrice(baseFee).
				logger.Trace("fail to validate gasprice", "pool.gasPrice", pool.gasPrice, "tx.gasPrice", tx.GasPrice())
				return ErrGasPriceBelowBaseFee
			}
		} else {
			// Unitprice policy before magma hardfork
			if pool.gasPrice.Cmp(tx.GasPrice()) != 0 {
				logger.Trace("fail to validate unitprice", "unitPrice", pool.gasPrice, "txUnitPrice", tx.GasPrice())
				return ErrInvalidUnitPrice
			}
		}
	}
```
