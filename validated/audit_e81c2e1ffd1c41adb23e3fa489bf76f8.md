## Title
Governance parameter boundary/consistency checks are bypassed when set via `GovParam` contract governance instead of header voting - ([File: kaiax/gov/param.go])

### Summary
`kaiax/gov/param.go` defines the `FormatChecker` used to validate every governance parameter value before it is accepted into a `ParamSet`. Several economically critical parameters — `GovernanceUnitPrice`, `IstanbulEpoch`, `Kip71GasTarget`, `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, and `Kip71MaxBlockGasUsedForBaseFee` — are configured with `noopFormatChecker`, which always returns `true` regardless of the value [1](#0-0) [2](#0-1) [3](#0-2) .

The cross-parameter sanity check that `Kip71LowerBoundBaseFee <= Kip71UpperBoundBaseFee` is only implemented in `headerGovModule.checkConsistency`, which is invoked exclusively during header-vote processing (`VerifyVote`) [4](#0-3) . This is exactly analogous to the reported bug class: an admin/owner-controlled setter that changes a fee/economic parameter without validating it against sane bounds or against the related parameter.

### Finding Description
Governance parameters can be set through two independent paths that are supposed to converge on the same `ParamSet`:
1. Header-vote governance (`governance_vote` → `headerGovModule.VerifyVote` → `checkConsistency`), which enforces `LowerBoundBaseFee <= UpperBoundBaseFee` [4](#0-3) .
2. Contract governance via the `GovParam` contract's `setParam`/`setParamIn` functions, which are restricted to the contract `owner` (an `Ownable` contract) [5](#0-4) .

When contract governance sets a value, it flows through `contractGetAllParamsAtFromAddr` → `ParseContractCall` → `PartialParamSet.Add`, which only calls `param.Canonicalizer` and `param.FormatChecker` [6](#0-5) [7](#0-6) . Because `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` (and `GovernanceUnitPrice`, `Kip71GasTarget`, `Kip71MaxBlockGasUsedForBaseFee`) use `noopFormatChecker`, there is **no** boundary check and **no** cross-check between Lower/Upper bounds in this path — unlike the header-vote path. The result is then merged unconditionally by `ParamSet.Set`, which only performs a Go type assertion, not a value-range check [8](#0-7) .

This mirrors the reported `CollateralBook.queueCollateralChange` issue: a privileged setter for a fee-relevant parameter accepts arbitrary values without validating them against protocol-required bounds or against a paired parameter.

### Impact Explanation
If `GovParam.setParam`/`setParamIn` sets `kip71.lowerboundbasefee > kip71.upperboundbasefee`, or sets `governance.unitprice` to `0`, this breaks invariants relied upon elsewhere:
- `KIP71Config.NextMagmaBlockBaseFee` clamps `parentBaseFee` between `lowerBoundBaseFee` and `upperBoundBaseFee` and assumes `lower <= upper` [9](#0-8) . With an inverted bound, the computed next base fee can violate the intended lower-bound floor, producing gas prices below the protocol-mandated minimum.
- `CNAPIBackend.LowerBoundGasPrice`/`UpperBoundGasPrice` directly expose these values to RPC clients and fee estimation, so an inverted or zeroed bound propagates into fee-related APIs and potentially into acceptance of underpriced transactions [10](#0-9) .
- `governance.unitprice` accepting `0` unconditionally (pre-Magma) removes the minimum tx fee entirely, i.e. a fee-bypass condition, since `GovernanceUnitPrice`'s only checker is `noopFormatChecker` [2](#0-1) .

This can lead to fee/economic-parameter corruption and potential fee bypass at the protocol level, matching the "Medium" severity classification and "fee ... abuse" category referenced in the validation rules.

### Likelihood Explanation
The `GovParam` contract owner (analogous to the "admin" role in the original report) can call `setParam`/`setParamIn` at any time with arbitrary byte-encoded values; the module-level code performs no sanity validation before merging the value into the live `ParamSet` used for block production and fee logic. No additional consensus or validator quorum is required for this path (contract governance is a single-owner action), only intent/misconfiguration or a compromised owner key is needed to trigger it — no cross-check like the header-vote path exists to catch it.

### Recommendation
Add value-bound and cross-parameter validation to the `FormatChecker` for `Kip71LowerBoundBaseFee`, `Kip71UpperBoundBaseFee`, `GovernanceUnitPrice`, `Kip71GasTarget`, and `Kip71MaxBlockGasUsedForBaseFee` in `kaiax/gov/param.go`, or move the `Lower <= Upper` consistency check into `ParamSet.Set`/`PartialParamSet.Add` so it is enforced uniformly regardless of whether the parameter originates from header voting or contract governance.

### Proof of Concept
1. Deploy `GovParam` and set it as `governance.govparamcontract` (as the existing test `deployGovParamTx_constructor`/`deployGovParamTx_setParamIn` flow demonstrates) [11](#0-10) .
2. As the `GovParam` owner, call `setParamIn("kip71.lowerboundbasefee", true, <bytes for a large uint64>, 1)` and `setParamIn("kip71.upperboundbasefee", true, <bytes for a small uint64>, 1)` such that lower > upper.
3. Observe that `contractGovModule.GetParamSet` accepts both values because `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` use `noopFormatChecker` and no consistency check runs in this code path (`checkConsistency` is only invoked from `headerGovModule.VerifyVote`) [4](#0-3) .
4. Once active, `KIP71Config.NextMagmaBlockBaseFee` computes fees using inverted/inconsistent bounds [9](#0-8) .

### Citations

**File:** kaiax/gov/param.go (L160-162)
```go
func noopFormatChecker(cv any) bool {
	return true
}
```

**File:** kaiax/gov/param.go (L259-264)
```go
	GovernanceUnitPrice: {
		Canonicalizer:    uint64Canonicalizer,
		FormatChecker:    noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) { return c.UnitPrice, nil },
		DefaultValue:     uint64(250e9),
	},
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

**File:** contracts/libs/openzeppelin-contracts-v2/contracts/ownership/Ownable.sol (L35-38)
```text
    modifier onlyOwner() {
        require(isOwner(), "Ownable: caller is not the owner");
        _;
    }
```

**File:** kaiax/gov/contractgov/impl/getter.go (L83-94)
```go
	ret := ParseContractCall(names, values)

	rules := config.Rules(new(big.Int).SetUint64(blockNum))
	for name := range ret {
		if gov.DeprecatedAt(name, rules) {
			logger.Warn("Ignoring deprecated parameter from contract governance", "name", name, "blockNum", blockNum)
			delete(ret, name)
		}
	}

	return ret, nil
}
```

**File:** kaiax/gov/paramset.go (L54-121)
```go
// Set the canonical value in the ParamSet for the corresponding parameter name.
func (p *ParamSet) Set(name ParamName, cv any) error {
	var (
		tmp *big.Int
		ok  bool
	)
	switch name {
	case GovernanceGovernanceMode:
		p.GovernanceMode, ok = cv.(string)
	case GovernanceGoverningNode:
		p.GoverningNode, ok = cv.(common.Address)
	case GovernanceGovParamContract:
		p.GovParamContract, ok = cv.(common.Address)
	case GovernanceUnitPrice:
		p.UnitPrice, ok = cv.(uint64)
	case IstanbulCommitteeSize:
		p.CommitteeSize, ok = cv.(uint64)
	case IstanbulEpoch:
		p.Epoch, ok = cv.(uint64)
	case IstanbulPolicy:
		p.ProposerPolicy, ok = cv.(uint64)
	case Kip71BaseFeeDenominator:
		p.BaseFeeDenominator, ok = cv.(uint64)
	case Kip71GasTarget:
		p.GasTarget, ok = cv.(uint64)
	case Kip71LowerBoundBaseFee:
		p.LowerBoundBaseFee, ok = cv.(uint64)
	case Kip71MaxBlockGasUsedForBaseFee:
		p.MaxBlockGasUsedForBaseFee, ok = cv.(uint64)
	case Kip71UpperBoundBaseFee:
		p.UpperBoundBaseFee, ok = cv.(uint64)
	case RewardDeferredTxFee:
		p.DeferredTxFee, ok = cv.(bool)
	case RewardKip82Ratio:
		p.Kip82Ratio, ok = cv.(string)
	case RewardMintingAmount:
		if tmp, ok = cv.(*big.Int); ok {
			p.MintingAmount = new(big.Int).Set(tmp)
		}
	case RewardMinimumStake:
		if tmp, ok = cv.(*big.Int); ok {
			p.MinimumStake = new(big.Int).Set(tmp)
		}
	case RewardProposerUpdateInterval:
		p.ProposerUpdateInterval, ok = cv.(uint64)
	case RewardRatio:
		p.Ratio, ok = cv.(string)
	case RewardStakingRewardThreshold:
		if tmp, ok = cv.(*big.Int); ok {
			p.StakingRewardThreshold = new(big.Int).Set(tmp)
		}
	case RewardStakingUpdateInterval:
		p.StakingUpdateInterval, ok = cv.(uint64)
	case RewardUseFlexReward:
		p.UseFlexReward, ok = cv.(bool)
	case RewardUseGiniCoeff:
		p.UseGiniCoeff, ok = cv.(bool)
	case GovernanceDeriveShaImpl:
		p.DeriveShaImpl, ok = cv.(uint64)
	default:
		return ErrInvalidParamName
	}

	if !ok {
		return ErrInvalidParamValue
	}
	return nil
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

**File:** params/kip71_config.go (L80-86)
```go
	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}
```

**File:** node/cn/api_backend.go (L333-351)
```go
func (b *CNAPIBackend) UpperBoundGasPrice(ctx context.Context) *big.Int {
	bignum := b.CurrentBlock().Number()
	pset := b.cn.govModule.GetParamSet(bignum.Uint64() + 1)
	if b.cn.chainConfig.IsMagmaForkEnabled(bignum) {
		return new(big.Int).SetUint64(pset.UpperBoundBaseFee)
	} else {
		return new(big.Int).SetUint64(pset.UnitPrice)
	}
}

func (b *CNAPIBackend) LowerBoundGasPrice(ctx context.Context) *big.Int {
	bignum := b.CurrentBlock().Number()
	pset := b.cn.govModule.GetParamSet(bignum.Uint64() + 1)
	if b.cn.chainConfig.IsMagmaForkEnabled(bignum) {
		return new(big.Int).SetUint64(pset.LowerBoundBaseFee)
	} else {
		return new(big.Int).SetUint64(pset.UnitPrice)
	}
}
```

**File:** tests/gov_contract_test.go (L154-197)
```go
func deployGovParamTx_constructor(t *testing.T, node *cn.CN, owner *TestAccountType, chainId *big.Int,
) (uint64, common.Address, *types.Transaction) {
	var (
		// Deploy contract: constructor(address _owner)
		contractAbi, _ = abi.JSON(strings.NewReader(govcontract.GovParamABI))
		contractBin    = govcontract.GovParamBin
		ctorArgs, _    = contractAbi.Pack("")
		code           = contractBin + hex.EncodeToString(ctorArgs)
	)

	// Deploy contract
	tx, addr := deployContractDeployTx(t, node.TxPool(), chainId, owner, code)

	chain := node.BlockChain().(*blockchain.BlockChain)
	receipt := waitReceipt(chain, tx.Hash())
	require.NotNil(t, receipt)
	require.Equal(t, types.ReceiptStatusSuccessful, receipt.Status)

	_, _, num, _ := chain.GetTxAndLookupInfo(tx.Hash())
	t.Logf("GovParam deployed at block=%2d, addr=%s", num, addr.Hex())

	return num, addr, tx
}

func deployGovParamTx_setParamIn(t *testing.T, node *cn.CN, owner *TestAccountType, chainId *big.Int,
	contractAddr common.Address, name string, value []byte,
) (uint64, *types.Transaction) {
	var (
		contractAbi, _ = abi.JSON(strings.NewReader(govcontract.GovParamABI))
		callArgs, _    = contractAbi.Pack("setParamIn", name, true, value, big.NewInt(1))
		data           = common.ToHex(callArgs)
	)

	tx := deployContractExecutionTx(t, node.TxPool(), chainId, owner, contractAddr, data)

	chain := node.BlockChain().(*blockchain.BlockChain)
	receipt := waitReceipt(chain, tx.Hash())
	require.NotNil(t, receipt)
	require.Equal(t, types.ReceiptStatusSuccessful, receipt.Status, "setParamIn failed")

	_, _, num, _ := chain.GetTxAndLookupInfo(tx.Hash())
	t.Logf("GovParam.setParamIn executed at block=%2d", num)
	return num, tx
}
```
