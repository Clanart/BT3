Found the key finding. This is a documented, known admission-time gas miscalculation in the EVM ante pipeline that directly mirrors the report's bug class: an admission-time gas check computed with the wrong (too-lenient) formula, distinct from the formula actually enforced during execution.

### Title
EVM ante admission uses pre-EIP-7623 intrinsic gas instead of floor data gas, admitting txs that are then rejected/burned in Execute() - ([File: x/evm/ante/basic.go], [File: app/ante/evm_checktx.go])

### Summary
The Sei EVM ante pipeline's stateless intrinsic-gas admission check (`core.IntrinsicGas`) uses the pre-EIP-7623 formula (`21000 + 4*data-tokens`), while go-ethereum's `Execute()` path enforces the stricter EIP-7623 "floor data gas" (`21000 + 10*data-tokens`) later in the pipeline. A transaction whose `Gas()` satisfies the lenient admission check but not the floor check is admitted into a block, fully charged the gas limit, and fails inside execution — a state-transition failure that occurs *after* admission succeeded, contrary to what senders/tooling using the admission formula expect. This is acknowledged by Sei itself as issue #4068 and skip-listed in the execution-spec test harness.

### Finding Description
`BasicDecorator.AnteHandle` (used in DeliverTx) and `EvmStatelessChecks` (used in CheckTx) both compute intrinsic gas via: [1](#0-0) [2](#0-1) 

This call to `core.IntrinsicGas(..., true, true, true)` computes only the legacy intrinsic-gas floor. The EIP-7623 "floor data gas" (a separate, higher minimum introduced to price calldata-heavy transactions) is enforced later, inside go-ethereum's `Execute()`/`StateTransition`, which runs only after the ante chain (fee charge, nonce bump) has already committed. Sei's own test suite documents the gap directly: [3](#0-2) 

And the exec-spec test runner explicitly skips upstream test vectors that exercise this exact discrepancy, citing issue #4068: [4](#0-3) [5](#0-4) 

This is structurally the same bug class as the Astaria M-7 report: an admission-time/validity gate computed with a different (weaker) formula than the one that governs whether the operation ultimately succeeds, so transactions that pass "the check that decides whether to accept the tx" nonetheless fail at the layer that actually enforces the real minimum — except here the direction is inverted (the ante check is too *lenient*, not too strict), producing guaranteed-to-fail admissions rather than guaranteed-to-revert-valid-terms.

### Impact Explanation
Any caller can construct a legacy/EIP-1559/EIP-2930 EVM transaction with a calldata length such that `intrinsic_gas <= Gas() < floor_data_gas`. Sei's ante chain admits it (fee is charged for the full gas limit, nonce is bumped), and go-ethereum's execution layer then fails it with "floor data gas", burning the entire gas limit for a transaction that senders following standard Ethereum intrinsic-gas tooling would reasonably expect to be a normal, executable transaction (or, conversely, expect `eth_estimateGas`/wallets computing the correct EIP-7623 floor to reject before signing, while Sei's own admission gate would have let a lower-gas version through). This causes unnecessary fee loss/gas burn for well-formed transactions and diverges Sei's on-chain admission semantics from upstream Ethereum, which the project's own execution-spec conformance harness had to special-case to avoid failing CI. It does not constitute a targeted DoS against a specific victim contract, but it is a confirmed, chain-wide, protocol-level divergence in transaction-admission gas accounting reachable by any public-RPC sender.

### Likelihood Explanation
High likelihood of being triggered incidentally (any calldata-heavy transaction with a tightly-sized gas limit relative to intrinsic gas) and trivially triggerable deliberately by any transaction sender, since the exact `dataLen`/`gasLimit` boundary is fully public and documented in-repo (`giga_test.go` comments give exact numbers: `dataLen=1000`, intrinsic=25000, floor=31000, `gasLimit=27500`).

### Recommendation
Move the EIP-7623 floor-data-gas check into the ante admission path (`BasicDecorator.AnteHandle` / `EvmStatelessChecks`), matching whatever fork rules are active, so that admission and execution enforce the same minimum-gas formula and calldata-heavy transactions are rejected pre-admission (no fee charged, no nonce bump) rather than admitted and then failed during execution.

### Proof of Concept [6](#0-5) 

Using `dataLen=1000` zero bytes and `gasLimit=27500`: intrinsic gas (EIP-2028, checked by `core.IntrinsicGas` in ante) = 25000, so the ante admission check `etx.Gas() < intrGas` (27500 < 25000) passes and the tx is admitted. go-ethereum's EIP-7623 floor-data-gas requirement for the same calldata is 31000, so `Execute()` fails with "floor data gas", and the sender is charged the full `gasLimit * effectiveGasPrice` with no execution refund, as directly asserted by the test's balance-delta check.

### Citations

**File:** x/evm/ante/basic.go (L51-57)
```go
	intrGas, err := core.IntrinsicGas(etx.Data(), etx.AccessList(), etx.SetCodeAuthorizations(), etx.To() == nil, true, true, true)
	if err != nil {
		return ctx, err
	}
	if etx.Gas() < intrGas {
		return ctx, core.ErrIntrinsicGas
	}
```

**File:** app/ante/evm_checktx.go (L113-119)
```go
	intrGas, err := core.IntrinsicGas(etx.Data(), etx.AccessList(), etx.SetCodeAuthorizations(), etx.To() == nil, true, true, true)
	if err != nil {
		return err
	}
	if etx.Gas() < intrGas {
		return core.ErrIntrinsicGas
	}
```

**File:** giga/tests/giga_test.go (L1839-1866)
```go
// TestGiga_FailedExecutionFallsBackToV2 verifies that state-transition errors
// are handled identically by v2, sequential Giga, and OCC Giga. EIP-7623's
// floor-data-gas check happens inside go-ethereum's Execute() after Sei's ante
// checks, so it exercises the execution-error fallback rather than validation.
func TestGiga_FailedExecutionFallsBackToV2(t *testing.T) {
	blockTime := time.Now()
	accts := utils.NewTestAccounts(3)
	signer := utils.NewSigner()

	// EIP-7623 floor:    21000 + 10 * data-tokens (zero byte = 1 token).
	// Intrinsic (EIP-2028): 21000 +  4 * data-tokens.
	// 1000 zero-byte payload → intrinsic=25000, floor=31000.
	// gasLimit=27500 passes EvmStatelessChecks' intrinsic check (>=25000) but fails
	// go-ethereum's floor-data-gas check inside Execute() (<31000).
	const dataLen = 1000
	const gasLimit uint64 = 27500
	to := common.HexToAddress("0x0000000000000000000000000000000000001234")
	signedTx, err := ethtypes.SignTx(ethtypes.NewTx(&ethtypes.DynamicFeeTx{
		ChainID:   big.NewInt(config.DefaultChainID),
		Nonce:     0,
		GasFeeCap: big.NewInt(100000000000),
		GasTipCap: big.NewInt(100000000000),
		Gas:       gasLimit,
		To:        &to,
		Value:     big.NewInt(0),
		Data:      make([]byte, dataLen),
	}), signer.EvmSigner, signer.EvmPrivateKey)
	require.NoError(t, err)
```

**File:** integration_test/exec_specs/plugin/eest_plugin.py (L54-60)
```python
def _eip7623_admission(item: pytest.Item) -> bool:
    return (
        "tests/prague/eip7623_increase_calldata_cost/test_transaction_validity.py"
        in item.nodeid
        and "insufficient_gas-floor_gas_greater_than_intrinsic_gas" in item.name
        and "-unprotected-" not in item.name
    )
```

**File:** integration_test/exec_specs/plugin/eest_plugin.py (L117-133)
```python
SKIP_RULES: tuple[SkipRule, ...] = (
    SkipRule(
        id="eip7623-admission",
        reason=(
            "Known Sei EIP-7623 transaction-admission issue "
            "(https://github.com/sei-protocol/sei-chain/issues/4068)."
        ),
        matches=_eip7623_admission,
    ),
    SkipRule(
        id="eip7623-floor-data-gas",
        reason=(
            "Known Sei EIP-7623 floor-data-gas admission issue "
            "(https://github.com/sei-protocol/sei-chain/issues/4068)."
        ),
        matches=_eip7623_floor_data_gas,
    ),
```
