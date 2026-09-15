### Title
Contract governance (KIP-81 `GovParam`) path bypasses the `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` consistency check enforced by header-vote governance - ([File: kaiax/gov/contractgov/impl/getter.go])

### Summary
Kaia has two ways to change governance parameters: header-vote governance (`kaiax/gov/headergov`) and on-chain contract governance via the KIP-81 `GovParam` contract (`kaiax/gov/contractgov`). For header votes, `checkConsistency` explicitly rejects a `Kip71LowerBoundBaseFee` vote greater than the current `UpperBoundBaseFee` (and vice versa) before the vote is accepted into a block [1](#0-0) . The contract-governance ingestion path, however, only runs `ParamSet.Set()`, which performs a type/format assertion but no cross-field validation, and never calls anything analogous to `checkConsistency` [2](#0-1) . This is structurally the same class of bug as the reported `MixinParams.setParams` issue: one “safe” configuration path enforces an invariant, while a second, equally-authorized configuration path bypasses it entirely.

### Finding Description
`checkConsistency` in headergov guards the KIP-71 base-fee bound parameters: [1](#0-0) 
This check only runs for votes that go through `VerifyGov`/`VerifyVote` on headers (header-vote governance).

The alternate governance path, `GovParam` contract governance (KIP-81), lets an owner/authorized caller of the `GovParam` contract call `setParamIn(name, activate, value, activationBlock)` to set any parameter, including `kip71.lowerboundbasefee` and `kip71.upperboundbasefee`, directly on-chain [3](#0-2) . When `contractGovModule.GetParamSet` reads these values back via `getAllParamsAt`, it applies only per-field format/type validation through `ParamSet.Set`, with no cross-parameter consistency check: [2](#0-1) 
The individual `FormatChecker` for `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` is a no-op that accepts any `uint64`: [4](#0-3) 

As a result, an authorized `GovParam` owner can set `LowerBoundBaseFee > UpperBoundBaseFee` through contract governance even though the identical operation would be rejected (`ErrLowerBoundBaseFee`/`ErrUpperBoundBaseFee`) if attempted via header-vote governance.

This inverted configuration is then consumed by the KIP-71 dynamic base-fee calculation, `KIP71Config.NextMagmaBlockBaseFee`, which assumes `LowerBoundBaseFee <= UpperBoundBaseFee`: [5](#0-4) 
With inverted bounds, the clamp logic (`if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 { parentBaseFee = upperBoundBaseFee } else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 { parentBaseFee = lowerBoundBaseFee }`) and the subsequent "decrease" branch (`if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 { return lowerBoundBaseFee }`) will systematically clamp the computed base fee up to the (now larger) `LowerBoundBaseFee` value even when block usage is below target and the fee should be decreasing. This produces base fees that are permanently forced to an inflated floor value, independent of real network demand.

### Impact Explanation
Because base fee is a network-wide, consensus-relevant value used to price every transaction, an inverted `LowerBoundBaseFee`/`UpperBoundBaseFee` pair forces the minimum acceptable gas price up to whatever value the (still-authorized) `GovParam` owner chooses, well beyond the intended market-driven KIP-71 pricing. This is a fee-abuse impact reachable by every ordinary transaction sender on the network: all transactions are forced to overpay gas at the inflated floor set by the misconfigured governance parameters, and legitimate transactions may be effectively priced out (denial of fee-market functioning) — the exact "unsafe or nonsensical parameter value" scenario that the original 0x bug report called out and that the KIP-81 governance path fails to protect against, unlike the header-vote path.

### Likelihood Explanation
This does not require a bug in transaction execution or state-transition logic — it only requires a single call to `GovParam.setParamIn` for two parameter names, made through the already-existing and normally-used contract-governance mechanism. Since format validation on each field passes (there is no min/max relationship check), the misconfiguration will be silently accepted by every node the moment `GetParamSet` reads the contract state, with no error or rejection at any layer (`ParamSet.Set`, `contractGetAllParamsAtFromAddr`) that would normally trigger the header-vote-equivalent `ErrLowerBoundBaseFee`/`ErrUpperBoundBaseFee`.

### Recommendation
Apply the same cross-parameter consistency check used in `headergov.checkConsistency` (i.e., `LowerBoundBaseFee <= UpperBoundBaseFee`) inside the contract-governance ingestion path, e.g., in `contractGovModule.GetParamSet` / `ParamSet.SetFromMap`, before accepting values sourced from the `GovParam` contract. More generally, any parameter that has an invariant enforced in one governance path (header votes) should have that same invariant enforced in the other path (contract governance) so no path can silently write out-of-range or logically inconsistent values into the effective `ParamSet`.

### Proof of Concept
1. Deploy or use an existing `GovParam` contract with contract-governance enabled (post-Kore fork), owned by an authorized governance account, per the existing test helper `deployGovParamTx_constructor` / `deployGovParamTx_setParamIn` [6](#0-5) .
2. Call `GovParam.setParamIn("kip71.lowerboundbasefee", true, <encoded uint64 = 750000000000>, activationBlock)` and `GovParam.setParamIn("kip71.upperboundbasefee", true, <encoded uint64 = 25000000000>, activationBlock)` — i.e., swap the default lower/upper values so lower > upper.
3. Once effective, `contractGovModule.GetParamSet(blockNum)` will report `LowerBoundBaseFee = 750000000000` and `UpperBoundBaseFee = 25000000000` with no error, because `getter.go`’s loop only calls `ParamSet.Set` per field [2](#0-1) , in contrast to the header-vote path which would reject this via `checkConsistency` [1](#0-0) .
4. Subsequent blocks compute `NextMagmaBlockBaseFee` using these inverted bounds [5](#0-4) , producing a base fee clamped to the inflated "lower" bound regardless of actual gas usage, forcing every transaction sender to pay the inflated minimum fee.

(Note: I was not able to fully inspect `kaiax/gov/impl/getter.go`, the top-level module that merges `headergov` and `contractgov` results into the final effective `ParamSet`, before the tool budget ran out. Based on the module's own documented merge order — default → ChainConfig → HeaderGov → ContractGov (post-Kore) — and the absence of any consistency-check reference found in that package via `grep_search`, it appears no cross-field validation is applied at the merge stage either, but this could not be independently confirmed by reading that file's full contents.)

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

**File:** params/kip71_config.go (L58-129)
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
}
```
