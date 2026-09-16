## Analog Found

### Title
Governance parameter ordering (LowerBoundBaseFee ≤ UpperBoundBaseFee) is enforced only in header governance but not in contract governance, allowing acceptance of inconsistent KIP-71 base-fee bounds - ([File: kaiax/gov/contractgov/impl/getter.go])

### Summary
The external report's bug class is "array/curve values not checked to be monotonically ordered before being consumed in downstream arithmetic, causing incorrect/reverting computations." Kaia's analog is the KIP-71 base-fee bound parameters `LowerBoundBaseFee`/`UpperBoundBaseFee`, which must satisfy `LowerBoundBaseFee <= UpperBoundBaseFee` for `NextMagmaBlockBaseFee` to behave correctly. This ordering invariant is enforced for header-based governance votes but is completely absent for contract-based governance (KIP-81 `GovParam` contract), creating an inconsistent validation gap between the two governance engines.

### Finding Description
The per-parameter `FormatChecker` for both bound parameters is a no-op that performs no cross-field validation: [1](#0-0) 

The only place that enforces `LowerBoundBaseFee <= UpperBoundBaseFee` is `checkConsistency` in the **header governance** module, invoked when a validator votes to change one of these two params: [2](#0-1) 

However, **contract governance** (`kaiax/gov/contractgov`), which reads parameter values directly from the on-chain `GovParam` contract (populated via KIP-81 `setParamIn` calls), never calls `checkConsistency` or performs any cross-parameter validation. It simply applies each raw value via `ParamSet.Set`: [3](#0-2) 

And `ParamSet.Set` for `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` performs a bare type assertion with no bounds/ordering check: [4](#0-3) 

The resulting `ParamSet` is converted directly into a `KIP71Config` and fed to `NextMagmaBlockBaseFee`, which assumes `LowerBoundBaseFee <= UpperBoundBaseFee` when clamping `parentBaseFee`: [5](#0-4) 

If `LowerBoundBaseFee > UpperBoundBaseFee` is set through contract governance, the clamping logic at lines 82-86 produces baseFee values that violate the governance-intended bounds (e.g., the effective base fee can be pinned below the configured "lower" bound because the smaller of the two values wins the clamp), and the genesis/fork-activation fallback `return makeEvenByFloor(lowerBoundBaseFee)` can return a value above the configured upper bound.

### Impact Explanation
KIP-71 base fee directly drives the network's fee/burn accounting: half of `execFee` is burnt (Magma/Kore rule) and validator/proposer rewards are computed off of it. An inconsistent bound configuration reachable via contract governance (a category explicitly in scope: "governance parameters") can force the network onto an unintended base-fee trajectory, understating or overstating fees paid by ordinary transaction senders and altering the burn/reward split for every block — a fee-accounting integrity issue reachable without needing header-vote privileges, since contract governance bypasses the ordering check that header governance enforces.

### Likelihood Explanation
Reaching this requires only that contract governance be active (post-Kore, `GovParamContract` set) and that whoever can call `setParamIn` on `GovParam` (a KIP-81 governance action, not restricted to being a strict superset of the header-vote-eligible validator set logic that performs `checkConsistency`) sets one of the two bound params without also updating the other consistently. Because the two engines have divergent validation, this is a straightforward configuration-path mismatch bug rather than a hypothetical edge case — it is a validated code-level gap, not just a report-derived guess.

### Recommendation
Add the same `LowerBoundBaseFee <= UpperBoundBaseFee` consistency check inside contract governance's parameter application path (e.g., in `contractGovModule.GetParamSet` after building `ret`, or centrally inside `gov.ParamSet.Set`/a shared post-set validation step), so both governance engines reject or ignore inconsistent bound configurations symmetrically, matching the protection already present in `kaiax/gov/headergov/impl/header.go`.

### Proof of Concept
1. Enable contract governance (Kore-compatible chain with `GovParamContract` set).
2. Via KIP-81 `GovParam.setParamIn`, set `kip71.lowerboundbasefee` to a value greater than the currently configured `kip71.upperboundbasefee` (no consistency check blocks this, unlike the header-vote path in `checkConsistency`).
3. Once activated, `contractGovModule.GetParamSet` returns a `ParamSet` with `LowerBoundBaseFee > UpperBoundBaseFee`.
4. `ParamSet.ToKip71Config()` passes these inconsistent bounds to `NextMagmaBlockBaseFee`, which computes a next-block base fee that no longer respects the intended governance bound semantics (e.g., can be clamped to a value below the configured "lower" bound), impacting fee burn and reward accounting for all subsequent blocks.

### Citations

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

**File:** kaiax/gov/paramset.go (L79-84)
```go
	case Kip71LowerBoundBaseFee:
		p.LowerBoundBaseFee, ok = cv.(uint64)
	case Kip71MaxBlockGasUsedForBaseFee:
		p.MaxBlockGasUsedForBaseFee, ok = cv.(uint64)
	case Kip71UpperBoundBaseFee:
		p.UpperBoundBaseFee, ok = cv.(uint64)
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
