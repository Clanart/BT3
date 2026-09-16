### Title
Silent fallback to hard-coded default governance parameters (UnitPrice/KIP-71/reward) when the on-chain GovParam contract call fails, masking transient/state-dependent errors as "governance disabled" - ([File: kaiax/gov/contractgov/impl/getter.go])

### Summary
`contractGetAllParamsAtFromAddr` in `kaiax/gov/contractgov/impl/getter.go` treats *any* failure of the `getAllParamsAt` EVM call as if contract governance were simply "disabled," logging a warning and returning `nil, nil` instead of propagating the error. `GetParamSet`/`GetPartialParamSet` then substitute the compiled-in default parameter set (`gov.GetDefaultGovernanceParamSet()`) for the real, on-chain-voted values, exactly mirroring the CVE-2026-31837 pattern where a resolver failure silently reverts to hardcoded defaults instead of failing safe or making the fallback explicit/consistent.

### Finding Description
`contractGetAllParamsAtFromAddr` calls the GovParam contract's `getAllParamsAt(blockNum)` view function through a local EVM call: [1](#0-0) 

Any failure of this call (out-of-gas due to state pruning/missing trie nodes, a transient EVM/state error, an ABI decode mismatch, or any other `contract.GetAllParamsAt` error) is swallowed and mapped to `nil, nil` — the exact same return value used for the legitimate "GovParamContract address not set" case: [2](#0-1) 

`GetParamSet` and `GetPartialParamSet` cannot distinguish "governance genuinely not configured" from "the call to a configured GovParam contract errored," and both paths cause the effective config to fall back to `gov.GetDefaultGovernanceParamSet()` (or to whatever was already merged by lower-precedence sources such as `HeaderGov`): [3](#0-2) 

This partial parameter set is merged on top of the `Fallback` (ChainConfig) and `HeaderGov` parameter sets in the top-level `GetParamSet(blockNum)`, which is the function consumed by KIP-71 base-fee computation, transaction-pool `UnitPrice` admission, staking/reward parameters, and committee size — i.e., consensus-relevant, tx-admission-relevant governance state: [4](#0-3) 

Because the EVM call is executed against each node's *local* state via `backends.NewBlockchainContractBackend`, its success/failure is a function of the local node's state availability/behavior at that moment (e.g., a node that has pruned/archived state differently, or hits a transient resource limit during the call) — unlike a pure network fetch, but structurally the same defect class as the JWKS advisory: an external/underlying resolution step's failure is silently coerced into "use hardcoded defaults" rather than being surfaced as an explicit degraded/error state.

### Impact Explanation
If contract-governed parameters (e.g., `governance.unitprice`, KIP-71 base-fee bounds/target, reward ratios) have been changed away from their compiled-in defaults via on-chain voting, but one node's `getAllParamsAt` call fails while other nodes' calls succeed, the failing node computes a different effective `ParamSet` than its peers. This produces:
- **State divergence between honest nodes**: differing `UnitPrice`/KIP-71 base-fee parameters lead to different admission decisions in the tx pool and different computed base fees during block assembly/validation, which can cause a minority of nodes to accept/reject transactions or blocks that the majority does not.
- **Acceptance of transactions that should be rejected** (or rejection of transactions that should be accepted) under the real governance-configured `UnitPrice`, if the node silently reverts to the lower/higher default unit price.

### Likelihood Explanation
The condition requires a real failure of the local EVM view call to the GovParam contract on a specific node while the contract is configured and other nodes succeed — this can happen for reasons under a caller's indirect influence (e.g., resource exhaustion, gas-limited call reverting, malformed/edge-case parameter encoding causing `ParseContractCall`/decode issues) even absent malicious intent, and the error-swallowing behavior is deterministic and easy to trigger by any condition that makes the local call error, not just malicious network manipulation, satisfying the "unprivileged caller reachable" and "governance parameters" analog categories.

### Recommendation
Distinguish "contract governance not configured" (zero/unset address, pre-Kore) from "contract call to a *configured* GovParam address failed." In the latter case, do not silently substitute `gov.GetDefaultGovernanceParamSet()`; instead propagate the error up so the caller can retry, refuse to advance/participate, or otherwise fail loudly/consistently rather than silently diverging on a per-node basis. At minimum, log at `Error`/`Crit` severity (not `Warn`) and consider caching/reusing the last successfully retrieved on-chain parameter set instead of falling back to compiled-in defaults when the address is known but the call transiently fails.

### Proof of Concept
1. Deploy/activate a GovParam contract and vote `governance.unitprice` (or a KIP-71 parameter) to a value different from `gov.GetDefaultGovernanceParamSet()`'s default, at some `blockNum`.
2. On a given node, induce `contract.GetAllParamsAt(nil, blockNum)` to return an error at that block (e.g., a transient state-read failure or an encoding edge case that the mocked/simulated backend surfaces as an error) while other nodes succeed.
3. Observe that the affected node's `contractGetAllParamsAtFromAddr` logs "getAllParams call failed" and returns `nil, nil`, exactly as if GovParam were not configured: [5](#0-4) 
4. That node's `GetParamSet`/`GetPartialParamSet` then computes `UnitPrice`/KIP-71 parameters using compiled-in defaults instead of the voted value, while peer nodes use the correct value — a divergent transaction/block acceptance criterion across the network for identical blocks, matching the `TestGetParamSet`/`TestGetParamSetIgnoresDeprecatedParam` test scaffolding already present for this code path: [6](#0-5)

### Citations

**File:** kaiax/gov/contractgov/impl/getter.go (L17-40)
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

func (c *contractGovModule) GetPartialParamSet(blockNum uint64) gov.PartialParamSet {
	m, err := c.contractGetAllParamsAt(blockNum)
	if err != nil {
		return nil
	}
	return m
}
```

**File:** kaiax/gov/contractgov/impl/getter.go (L42-53)
```go
func (c *contractGovModule) contractGetAllParamsAt(blockNum uint64) (gov.PartialParamSet, error) {
	addr, err := c.contractAddrAt(blockNum)
	if err != nil {
		return nil, err
	}
	if common.EmptyAddress(addr) {
		logger.Trace("ContractEngine disabled: GovParamContract address not set")
		return nil, nil
	}

	return c.contractGetAllParamsAtFromAddr(blockNum, addr)
}
```

**File:** kaiax/gov/contractgov/impl/getter.go (L66-76)
```go
	caller := backends.NewBlockchainContractBackend(chain, nil, nil)
	contract, err := govcontract.NewGovParamCaller(addr, caller)
	if err != nil {
		return nil, err
	}

	names, values, err := contract.GetAllParamsAt(nil, new(big.Int).SetUint64(blockNum))
	if err != nil {
		logger.Warn("ContractEngine disabled: getAllParams call failed", "err", err)
		return nil, nil
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

**File:** kaiax/gov/contractgov/impl/getter_test.go (L98-123)
```go
func TestGetParamSet(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlError)
	name := string(gov.GovernanceUnitPrice)
	accounts, sim, addr, gp := createSimulateBackend(t)
	cgm := prepareContractGovModule(t, sim.BlockChain(), addr)

	setParam(t, sim, gp, accounts[0], name, []byte{0, 0, 0, 0, 0, 0, 0, 25}, 1000)
	assert.Equal(t, uint64(25), cgm.GetParamSet(1000).UnitPrice)

	setParam(t, sim, gp, accounts[0], name, []byte{0, 0, 0, 0, 0, 0, 0, 125}, 2000)
	assert.Equal(t, uint64(125), cgm.GetParamSet(2000).UnitPrice)
}

// reward.deferredtxfee (gov.AlwaysDeprecated): always dropped from contract governance.
func TestGetParamSetIgnoresDeprecatedParam(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlError)
	accounts, sim, addr, gp := createSimulateBackend(t)
	cgm := prepareContractGovModuleWithConfig(t, sim.BlockChain(), addr,
		&params.ChainConfig{KoreCompatibleBlock: big.NewInt(100), PermissionlessCompatibleBlock: big.NewInt(2000)})

	setParam(t, sim, gp, accounts[0], string(gov.RewardDeferredTxFee), []byte{0x01}, 200)

	deferredTxFee := gov.GetDefaultGovernanceParamSet().DeferredTxFee
	assert.Equal(t, deferredTxFee, cgm.GetParamSet(1500).DeferredTxFee) // ignored
	assert.Equal(t, deferredTxFee, cgm.GetParamSet(2500).DeferredTxFee) // ignored
}
```
