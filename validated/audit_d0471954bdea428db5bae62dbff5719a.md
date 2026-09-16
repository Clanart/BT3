### Title
Unbounded `governance.unitprice` governance parameter allows arbitrary/excessive network-wide transaction fees - ([File: kaiax/gov/param.go])

### Summary
The Kaia governance parameter `GovernanceUnitPrice` (the network's fixed gas price used before the Magma hard fork, and still an authoritative parameter fed into `TxPool.gasPrice`) has **no upper-bound (or even sanity) format check**, unlike its KIP-71 counterparts `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, which are explicitly cross-validated against each other. This mirrors the reported "IMPROPER UPPER BOUND ON THE FEE DEFINITION" bug class: a fee-setting parameter that is accepted into the protocol state without any bound, letting a single governance vote force every transaction sender on the network to pay an arbitrarily large, attacker-chosen gas price.

### Finding Description
`GovernanceUnitPrice` is registered with a `noopFormatChecker`, which always returns `true` regardless of value: [1](#0-0) 

Compare this to `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, which are cross-checked for consistency in `checkConsistency`: [2](#0-1) 

`GovernanceUnitPrice` falls into the catch-all branch of `checkConsistency` that performs no additional bound check beyond the no-op `FormatChecker`, and its canonicalizer (`uint64Canonicalizer`) only restricts the value's type/range to `uint64`, not to any protocol-reasonable ceiling: [3](#0-2) 

The accepted `UnitPrice` is used directly as the transaction pool's enforced/fixed gas price. For legacy/EthTxType transactions before Magma, the pool requires the transaction's `gasPrice` to *exactly equal* `pool.gasPrice`, which is initialized from `pset.UnitPrice`: [4](#0-3) [5](#0-4) 

Since there is no upper bound enforced anywhere in the governance vote pipeline (`NewVoteData` format check → `checkConsistency`), a validator/governing-council vote can push `UnitPrice` to an extreme `uint64` value (e.g., `math.MaxUint64`). Once ratified into `ParamSet`, every subsequent legacy transaction submitted by ordinary users must pay `gas * UnitPrice` in `buyGas()`, and Ethereum-typed transactions must set `maxFeePerGas`/`maxPriorityFeePerGas` equal to this value: [6](#0-5) 

This is functionally identical to the reported Bridge bug: a privileged actor can set a fee parameter with no protocol-enforced ceiling, and every unprivileged transaction sender is forced to pay it (or is priced out of the network entirely) once the parameter takes effect.

### Impact Explanation
Because `UnitPrice` is mandatory (not just a floor) for pre-Magma-type legacy/EIP-1559-typed transactions, an excessively large value would effectively:
- Force every ordinary transaction sender to pay an arbitrarily large fee to get any transaction included, or
- Make the network practically unusable/DoS'd for value-transfer and contract-call transactions, since senders can't opt out of the exact-match gas price requirement.

This directly matches the reported bug's impact category (excessive fees, financial burden, centralization risk) but at the level of the entire chain's fee market rather than a single bridge contract.

### Likelihood Explanation
Changing `GovernanceUnitPrice` requires governance-vote privilege (a validator/governing node casting a vote, as with other governance parameters explicitly listed in-scope). This is a privileged but still single-transaction/vote action with no additional bound check in the code path, so the likelihood is tied to governance vote submission rather than any complex multi-step exploit — same class of "single admin call, no upper bound" root cause as the original report.

### Recommendation
Add an explicit upper bound (and possibly overflow-safety) check for `GovernanceUnitPrice`, similar to the cross-validation already implemented for `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, e.g., enforce `UnitPrice <= some MaxUnitPrice constant` in both the `FormatChecker` for `GovernanceUnitPrice` in `kaiax/gov/param.go` and/or the `checkConsistency` switch in `kaiax/gov/headergov/impl/header.go`.

### Proof of Concept
1. A governance/validator node submits a vote transaction (as in `TestVerifyVote`) with `gov.GovernanceUnitPrice` set to a very large `uint64`, e.g. `18446744073709551615` (`math.MaxUint64`): [7](#0-6) 
2. `NewVoteData` accepts this value because the `FormatChecker` is a no-op for `GovernanceUnitPrice`, and `checkConsistency` performs no bound check on it, unlike `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`.
3. Once the vote is finalized at an epoch block, `ParamSet.UnitPrice` becomes the new fixed gas price, and `TxPool.gasPrice` / `pool.gasPrice` in `blockchain/tx_pool.go` is updated to this extreme value.
4. Every subsequent user transaction must set `gasPrice` (or `maxFeePerGas`/`maxPriorityFeePerGas`) to match this value exactly to be admitted to the pool, forcing all senders to pay an astronomically large fee in `buyGas()`.

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

**File:** blockchain/tx_pool.go (L288-299)
```go
	pool := &TxPool{
		config:                config,
		chainconfig:           chainconfig,
		chain:                 chain,
		signer:                types.LatestSignerForChainID(chainconfig.ChainID),
		pending:               make(map[common.Address]*txList),
		queue:                 make(map[common.Address]*txList),
		beats:                 make(map[common.Address]time.Time),
		all:                   newTxLookup(),
		pendingNonce:          make(map[common.Address]uint64),
		chainHeadCh:           make(chan ChainHeadEvent, chainHeadChanSize),
		gasPrice:              new(big.Int).SetUint64(pset.UnitPrice),
```

**File:** blockchain/tx_pool.go (L875-881)
```go
		} else {
			// Unitprice policy before magma hardfork
			if pool.gasPrice.Cmp(tx.GasPrice()) != 0 {
				logger.Trace("fail to validate unitprice", "unitPrice", pool.gasPrice, "txUnitPrice", tx.GasPrice())
				return ErrInvalidUnitPrice
			}
		}
```

**File:** blockchain/state_transition.go (L302-341)
```go
	// mgval is the maximum gas fee that can actually be paid in the worst case (e.g., revert)
	// st.gasPrice = tx.gasPrice (before Magma) or effectiveGasPrice (since Magma)
	mgval := new(big.Int).Mul(new(big.Int).SetUint64(st.msg.Gas()), st.gasPrice)

	// feeCap is the maximum gas fee the sender was willing to pay
	// GasFeeCap = tx.maxFeePerGas (if exists) or tx.gasPrice
	feeCap := new(big.Int).Mul(new(big.Int).SetUint64(st.msg.Gas()), st.msg.GasFeeCap())

	if isOsaka {
		if blobGas := st.blobGasUsed(); blobGas > 0 {
			// Check that the user has enough funds to cover blobGasUsed * tx.BlobGasFeeCap
			blobFeeCap := new(big.Int).SetUint64(blobGas)
			blobFeeCap.Mul(blobFeeCap, st.msg.BlobGasFeeCap())
			feeCap.Add(feeCap, blobFeeCap)
			// Pay for blobGasUsed * actual blob fee
			blobFee := new(big.Int).SetUint64(blobGas)
			blobFee.Mul(blobFee, st.evm.Context.BlobBaseFee)
			mgval.Add(mgval, blobFee)
		}
	}

	if validatedFeePayer == validatedSender {
		// 1. Non fee-delegated tx
		// 2. FeeDelegatedWithRatio with sender == feePayer
		// 3. FeeDelegated          with sender == feePayer

		// Before Osaka, only the check of the amount to be deducted is applied.
		// Therefore, all options are false.
		checkOverflow, checkWithValue := false, false
		balanceCheck := mgval
		if isOsaka {
			// Overflow will be checked from osaka onwards.
			// A value check is also performed.
			checkOverflow, checkWithValue = true, true
			balanceCheck = feeCap
		}
		if err := st.checkFeePayerBalance(balanceCheck, checkOverflow, checkWithValue); err != nil {
			return err
		}
		st.state.SubBalance(validatedFeePayer, mgval)
```

**File:** kaiax/gov/headergov/impl/header_test.go (L84-84)
```go
		{desc: "valid unitprice", vote: headergov.NewVoteData(validVoter, string(gov.GovernanceUnitPrice), uint64(25000000000)), expectedError: nil},
```
