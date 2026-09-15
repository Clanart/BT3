### Title
Missing Cross-Field Invariant Check Between `LowerBoundBaseFee` and `UpperBoundBaseFee` in Contract Governance Parameter Ingestion - (File: kaiax/gov/contractgov/impl/getter.go)

### Summary
The KIP-71 (Magma) dynamic base fee mechanism requires `LowerBoundBaseFee <= UpperBoundBaseFee` (analogous to DODOMath's `V0 >= V1 >= V2 > 0` invariant) to be well-defined. This ordering is explicitly validated for header-governance votes, but the same invariant is never checked when the equivalent parameters are supplied via **contract governance** (the on-chain `GovParam` contract, KIP-81), allowing an inconsistent parameter set to reach `KIP71Config.NextMagmaBlockBaseFee`.

### Finding Description
For header-governance votes, `checkConsistency` explicitly enforces the ordering invariant between the two parameters: [1](#0-0) 

However, the parameter set as seen by block processing is a merge of `Fallback → HeaderGov → ContractGov` (post-Kore), where each source's values are applied independently via `ParamSet.Set(k, v)`: [2](#0-1) 

The contract-governance path (`kaiax/gov/contractgov/impl/getter.go`) reads `LowerBoundBaseFee`/`UpperBoundBaseFee` from the `GovParam` contract and applies each individually with no cross-field check: [3](#0-2) 

The per-parameter `FormatChecker` for both `Kip71LowerBoundBaseFee` and `Kip71UpperBoundBaseFee` is `noopFormatChecker` — it only validates that the value canonicalizes to `uint64`, with no bound relative to the other field: [4](#0-3) 

Because contract governance parameters are applied to the merged `ParamSet` independently and after header-governance values (and can even fully override a "valid" header-gov pair with an inconsistent pair), it is possible for `LowerBoundBaseFee > UpperBoundBaseFee` to reach `ToKip71Config()` → `NextMagmaBlockBaseFee`, which assumes the ordering holds: [5](#0-4) 

With this invariant broken, e.g. when the parent-gas-used-below-target branch is taken, the code clips the computed next base fee to `lowerBoundBaseFee` whenever it falls below it: [6](#0-5) 

If `lowerBoundBaseFee > upperBoundBaseFee`, this clip can push (and pin) the effective base fee to a value that is inconsistent with — and can exceed — the nominal "upper" bound, permanently distorting the KIP-71 fee curve. This computation is also the basis of consensus-critical header validation, `VerifyMagmaHeader`, which every node deterministically recomputes to validate `header.BaseFee`: [7](#0-6) [8](#0-7) 

### Impact Explanation
Since `NextMagmaBlockBaseFee` is deterministic and used identically by all nodes to both propose and verify the base fee, breaking the ordering invariant does not directly cause a fork/consensus divergence between honest nodes running the same code — but it does corrupt the fee-market economics that KIP-71 is designed to enforce: the base fee can be driven to (and pinned at) a value outside the intended bounds/direction, defeating the anti-congestion/anti-spam design and materially mispricing transaction fees network-wide (fee abuse / incorrect fee accounting), which is the same class of harm the reported DODOMath issue describes (invalid mathematical inputs propagating into critical financial computations).

### Likelihood Explanation
Reaching this requires a GovParam contract update via KIP-81 on-chain voting for `kip71.lowerboundbasefee`/`kip71.upperboundbasefee` — a real, reachable governance transaction path in scope ("governance parameters" and "pool admission and KIP-71 pricing" are explicitly listed in-scope topics). Unlike the header-vote path, this path performs no ordering validation at all, so a single malformed (or maliciously crafted) parameter update is sufficient; no additional privilege escalation beyond normal contract-governance parameter-setting rights is needed.

### Recommendation
Add a cross-field invariant check in the parameter-merging/consistency logic that applies uniformly to both header-gov and contract-gov sources — e.g. after computing the effective merged `ParamSet` in `kaiax/gov/impl/getter.go` (or within `ParamSet.Set`/`ToKip71Config`), reject or fall back to defaults if `LowerBoundBaseFee > UpperBoundBaseFee`, mirroring the check already present in `checkConsistency` for header-gov votes.

### Proof of Concept
1. A GC member/governance participant with `GovParam` contract voting rights submits a KIP-81 `setParam` transaction (or two) setting `kip71.lowerboundbasefee = X` and `kip71.upperboundbasefee = Y` with `X > Y`.
2. `contractGovModule.GetParamSet`/`GetPartialParamSet` (`kaiax/gov/contractgov/impl/getter.go:17-32`) applies both values with only `noopFormatChecker`, performing no ordering validation.
3. `GovModule.GetParamSet` (`kaiax/gov/impl/getter.go:7-37`) merges this contract-gov partial set on top of any header-gov values (post-Kore), yielding an effective `ParamSet` with `LowerBoundBaseFee > UpperBoundBaseFee`.
4. `ParamSet.ToKip71Config()` passes this inconsistent config into `KIP71Config.NextMagmaBlockBaseFee` (`params/kip71_config.go:58`), where the "below target" branch's lower-bound clamp (`params/kip71_config.go:110-128`) can pin the base fee to a value that violates/exceeds the intended upper bound, corrupting the fee curve for all subsequent blocks until governance corrects the values.

### Citations

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

**File:** kaiax/gov/impl/getter.go (L7-37)
```go
func (m *GovModule) GetParamSet(blockNum uint64) gov.ParamSet {
	ret := gov.GetDefaultGovernanceParamSet()

	p0 := m.Fallback
	for k, v := range p0 {
		err := ret.Set(k, v)
		if err != nil {
			logger.CritWithStack("Failed to add param from Fallback", "name", k, "value", v, "error", err)
		}
	}

	p1 := m.Hgm.GetPartialParamSet(blockNum)
	for k, v := range p1 {
		err := ret.Set(k, v)
		if err != nil {
			logger.CritWithStack("Failed to add param from HeaderGov", "name", k, "value", v, "error", err)
		}
	}

	if m.isKoreHF(blockNum) {
		p2 := m.Cgm.GetPartialParamSet(blockNum)
		for k, v := range p2 {
			err := ret.Set(k, v)
			if err != nil {
				logger.CritWithStack("Failed to add param from ContractGov", "name", k, "value", v, "error", err)
			}
		}
	}

	return *ret
}
```

**File:** kaiax/gov/contractgov/impl/getter.go (L17-32)
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
}
```

**File:** kaiax/gov/param.go (L335-367)
```go
	Kip71LowerBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.LowerBoundBaseFee, nil
		},
		DefaultValue: uint64(25000000000),
	},
	Kip71MaxBlockGasUsedForBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.MaxBlockGasUsedForBaseFee, nil
		},
		DefaultValue: uint64(60000000),
	},
	Kip71UpperBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.UpperBoundBaseFee, nil
		},
		DefaultValue: uint64(750000000000),
	},
```

**File:** params/kip71_config.go (L45-56)
```go
func (kc *KIP71Config) VerifyMagmaHeader(headerBaseFee *big.Int, parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) error {
	if headerBaseFee == nil {
		return fmt.Errorf("header is missing baseFee")
	}
	// Verify the baseFee is correct based on the parent header.
	expectedBaseFee := kc.NextMagmaBlockBaseFee(parentHeaderNumber, parentHeaderBaseFee, parentHeaderGasUsed)
	if headerBaseFee.Cmp(expectedBaseFee) != 0 {
		return fmt.Errorf("invalid baseFee: have %s, want %s, parentBaseFee %s, parentGasUsed %d",
			headerBaseFee, expectedBaseFee, parentHeaderBaseFee, parentHeaderGasUsed)
	}
	return nil
}
```

**File:** params/kip71_config.go (L58-86)
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
```

**File:** params/kip71_config.go (L110-128)
```go
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
