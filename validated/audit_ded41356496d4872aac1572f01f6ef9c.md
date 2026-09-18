### Title
Balance-check scope mismatch between giga fast-path validation and v2 EVM ante allows divergent acceptance of the same transaction - (File: app/app.go, x/evm/ante/fee.go / app/ante/evm_checktx.go)

### Summary
This is the same bug class as the report: two code paths that are supposed to gate the same operation apply different solvency conditions, so one path accepts what the other rejects. In sei-chain, `validateGigaEVMTx` (giga's fast pre-check path) requires `senderBalance >= gas*gasFeeCap + value`, while the v2 ante pipeline's `BuyGas()` (invoked from `EVMFeeCheckDecorator.AnteHandle` and `EvmCheckAndChargeFees`) only debits/validates `gas*gasFeeCap` (the fee), deferring the `value` transfer check to EVM execution itself.

### Finding Description
`app.go`'s `validateGigaEVMTx` computes: [1](#0-0) 
and rejects the transaction with `ErrInsufficientFunds` if `senderBalance < gas*gasFeeCap + value`, explicitly commenting that this "matches V2's `st.BuyGas()`" [2](#0-1) .

However, the v2 ante path only calls go-ethereum's `StateTransition.BuyGas()` (which reserves/validates only the gas fee, not `value`) via `EVMFeeCheckDecorator.AnteHandle`: [3](#0-2) 
and via `EvmCheckAndChargeFees`: [4](#0-3) 

The Sei test suite explicitly documents this discrepancy as a known "scope mismatch": giga's pre-check rejects `fee+value > balance` while v2's ante only requires `fee`, letting the transaction proceed to EVM execution for its outcome: [5](#0-4) 

The mitigation in place is a "fallback [that] makes giga adopt v2's receipt, keeping the executors code- and hash-identical for this class," per the same test comment. This mirrors exactly the structure of the reported bug: one code path's solvency/admission check (`adjust()`-equivalent = giga precheck) is stricter/different than the other's (`liquidate()`-equivalent = v2 `BuyGas`), and only an explicit reconciliation (fallback-to-v2 receipt) prevents divergence from actually manifesting as a consensus-affecting difference.

### Impact Explanation
If the fallback reconciliation is incomplete, has an edge case, or is bypassed for any transaction shape (e.g., different tx types, EIP-7702 set-code transactions, or unassociated-address balance aggregation which giga additionally accounts for but v2's `BuyGas` does not), the two execution paths (giga sequential/parallel vs. v2 sequential) would produce different accepted/rejected results and different resulting state (nonce bump, gas charged, receipt code) for the identical transaction. Because giga and v2 are meant to be two interchangeable/parallel execution engines whose outputs must be deterministically identical (as evidenced by `CompareDeterministicFields`/`CompareLastResultsHash` in the same test), any residual divergence in balance-sufficiency logic is a direct root cause for non-determinism between engines, which — if it were ever to occur between two nodes running different engines, or between a node's own validation and re-validation passes — would produce differing `ExecTxResult` codes/logs and app hashes, i.e. a chain split / consensus failure. This is a High-severity class of bug (fund/receipt outcome divergence and potential app-hash mismatch), matching the report's core observation that split solvency-check logic causes one code path to treat a transaction as acceptable while the other does not.

### Likelihood Explanation
The codebase authors already recognized this specific discrepancy is possible (hence the dedicated test `TestGigaValidation_ValueExceedsBalance_LastResultsHash` and an explicit fallback mechanism), which confirms the underlying inconsistency is real and reachable by any ordinary user submitting an EVM transaction whose balance covers the fee but not fee+value. The residual risk is limited to whatever edge cases the fallback-to-v2 reconciliation does not fully cover; I could not fully verify the fallback's completeness (e.g. whether it also correctly reconciles the unassociated-address dual-balance aggregation path unique to giga's precheck, lines 2939-2946) within the available context.

### Recommendation
Ensure `validateGigaEVMTx`'s pre-check either (a) exactly mirrors go-ethereum's `BuyGas` semantics (fee-only, deferring `value` sufficiency to execution, matching v2), or (b) if a stricter combined `fee+value` precheck is intentionally kept for performance, guarantee the fallback-to-v2-receipt path is unconditionally exercised for every case where the two checks could diverge — including EIP-7702 set-code transactions and unassociated-address dual-balance sources — and add explicit invariant tests asserting hash/receipt identity across all such edge cases, not just the plain-transfer case currently tested.

### Proof of Concept
Not independently reproducible from static analysis alone; the divergence is demonstrated by the existing regression test itself: [6](#0-5) 
which funds an account with `fund = 3e15`, covering the max fee (`2.1e15`) but not `fee+value` (`value = 2e15`), and compares v2 vs giga results — confirming the two paths would diverge absent the fallback reconciliation, exactly analogous to the `adjust()`/`liquidate()` mismatch in the source report.

### Citations

**File:** app/app.go (L2927-2933)
```go
	// ========================================================================
	// Balance check (matches V2's st.BuyGas())
	// ========================================================================

	// Insufficient balance for gas + value
	balanceCheck := new(big.Int).Mul(new(big.Int).SetUint64(ethTx.Gas()), ethTx.GasFeeCap())
	balanceCheck.Add(balanceCheck, ethTx.Value())
```

**File:** x/evm/ante/fee.go (L81-92)
```go
	st := core.NewStateTransition(evmInstance, emsg, &gp, true, false)
	// run stateless checks before charging gas (mimicking Geth behavior)
	if !ctx.IsCheckTx() && !ctx.IsReCheckTx() {
		// we don't want to run nonce check here for CheckTx because we have special
		// logic for pending nonce during CheckTx in sig.go
		if err := st.StatelessChecks(); err != nil {
			return ctx, sdkerrors.Wrap(sdkerrors.ErrWrongSequence, err.Error())
		}
	}
	if err := st.BuyGas(); err != nil {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, err.Error())
	}
```

**File:** app/ante/evm_checktx.go (L311-320)
```go
	st := core.NewStateTransition(evmInstance, emsg, &gp, true, false)
	if statelessChecks {
		if err := st.StatelessChecks(); err != nil {
			return nil, sdkerrors.Wrap(sdkerrors.ErrWrongSequence, err.Error())
		}
	}
	if err := st.BuyGas(); err != nil {
		return nil, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, err.Error())
	}
	return stateDB, nil
```

**File:** giga/tests/giga_test.go (L2583-2587)
```go
// TestGigaValidation_ValueExceedsBalance_LastResultsHash covers the balance
// check's scope mismatch: giga's pre-check rejects fee+value > balance while
// v2's ante only requires the fee, running the tx to its EVM outcome. The
// fallback makes giga adopt v2's receipt, keeping the executors code- and
// hash-identical for this class too.
```

**File:** giga/tests/giga_test.go (L2588-2615)
```go
func TestGigaValidation_ValueExceedsBalance_LastResultsHash(t *testing.T) {
	blockTime := time.Now()
	accts := utils.NewTestAccounts(3)
	signer := utils.NewSigner()
	recipient := utils.NewSigner()
	to := recipient.EvmAddress

	normalFee := big.NewInt(100000000000)
	fund := big.NewInt(3e15)  // covers the 2.1e15 max fee...
	value := big.NewInt(2e15) // ...but not fee+value

	v2Ctx := NewGigaTestContext(t, accts, blockTime, 1, ModeV2Sequential)
	fundAccount(t, v2Ctx, signer.AccountAddress, fund)
	v2Ctx.TestApp.EvmKeeper.SetAddressMapping(v2Ctx.Ctx, signer.AccountAddress, signer.EvmAddress)
	v2Tx := createCustomEVMTx(t, v2Ctx, signer, &to, value, 21000, normalFee, normalFee, 0)
	_, v2Results, _ := RunBlock(t, v2Ctx, [][]byte{v2Tx})

	gigaCtx := NewGigaTestContext(t, accts, blockTime, 1, ModeGigaSequential)
	fundAccount(t, gigaCtx, signer.AccountAddress, fund)
	gigaCtx.TestApp.GigaEvmKeeper.SetAddressMapping(gigaCtx.Ctx, signer.AccountAddress, signer.EvmAddress)
	gigaTx := createCustomEVMTx(t, gigaCtx, signer, &to, value, 21000, normalFee, normalFee, 0)
	_, gigaResults, _ := RunBlock(t, gigaCtx, [][]byte{gigaTx})

	require.Len(t, v2Results, 1)
	require.Len(t, gigaResults, 1)
	CompareDeterministicFields(t, "ValueExceedsBalance", v2Results, gigaResults)
	CompareLastResultsHash(t, "ValueExceedsBalance", v2Results, gigaResults)
}
```
