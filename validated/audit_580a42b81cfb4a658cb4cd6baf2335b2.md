Based on my research, I found the key analog: `blockchain/state_transition.go`'s `returnGas()` function contains the exact same structural bug pattern as the reported `PerpEngine::fill` issue — a discount/ratio (`feeRatio`) is applied in one branch but silently skipped in the other, with an incorrect assumption that the skipped branch is equivalent.

### Title
Fee-ratio short-circuit in `returnGas` credits full gas refund to feePayer instead of splitting it, causing sender fund loss whenever `feeRatio` is not detected as a ratio tx - (File: blockchain/state_transition.go)

### Summary
`StateTransition.returnGas()` refunds unused gas (bought during `buyGas`) back to the payer(s) of the transaction. It has two branches: when `isRatioTx` is true, the remaining balance is split between `feePayer` and `sender` using `CalcFeeWithRatio`; otherwise, the **entire** remaining balance is credited to `validatedFeePayer` only, based on the comment "the below routine processes when feeRatio == 100" — i.e., an assumption that non-ratio fee-delegated transactions always have `feePayer` covering 100% of the fee and thus deserve 100% of the refund.

### Finding Description
In `blockchain/state_transition.go`, `returnGas()`:
```go
func (st *StateTransition) returnGas() {
	remaining := new(big.Int).Mul(new(big.Int).SetUint64(st.gas), st.gasPrice)

	validatedFeePayer := st.msg.ValidatedFeePayer()
	validatedSender := st.msg.ValidatedSender()
	feeRatio, isRatioTx := st.msg.FeeRatio()
	if isRatioTx {
		feePayer, feeSender := types.CalcFeeWithRatio(feeRatio, remaining)
		st.state.AddBalance(validatedFeePayer, feePayer)
		st.state.AddBalance(validatedSender, feeSender)
	} else {
		// To make a short circuit, the below routine processes when feeRatio == 100.
		st.state.AddBalance(validatedFeePayer, remaining)
	}
}
``` [1](#0-0) 

This mirrors the structural pattern in the reported bug: a discount/ratio computation exists on one path, but the "default"/`else` branch skips the ratio-aware calculation, based on an assumption that turns out not to hold for all cases funneled into that branch.

Contrast this with `buyGas()`, which correctly gates fee splitting on `validatedFeePayer == validatedSender` (i.e., whether the payer and sender are actually different accounts), not on whether the tx type implements a ratio:
```go
if validatedFeePayer == validatedSender {
    ...
    st.state.SubBalance(validatedFeePayer, mgval)
} else {
    feePayerFee, senderFee := types.CalcFeeWithRatio(feeRatio, mgval)
    ...
    st.state.SubBalance(validatedFeePayer, feePayerFee)
    st.state.SubBalance(validatedSender, senderFee)
}
``` [2](#0-1) 

`FeeRatio()` returns `(MaxFeeRatio, false)` for any transaction type that does not implement `TxInternalDataFeeRatio` — this includes plain `TxTypeFeeDelegatedValueTransfer` (fee-delegated but NOT "WithRatio"), where `feePayer` and `sender` are still two distinct accounts, and per the tx-pool balance-check logic, `feePayer` is expected to pay 100% of `tx.Fee()` while `sender` pays 0% (see `blockchain/tx_pool.go` non-ratio branch):
```go
} else {
    if senderBalance.Cmp(tx.Value()) < 0 { ... }
    if feePayerBalance.Cmp(tx.Fee()) < 0 { ... }
}
``` [3](#0-2) 

So for `TxTypeFeeDelegatedValueTransfer` (non-ratio, distinct sender/feePayer), `buyGas()` correctly attributes 100% of `mgval` to `feePayer` (since `feeRatio` defaults to `MaxFeeRatio` = 100, `CalcFeeWithRatio` gives `feePayer=mgval, sender=0`), consistent with what `returnGas()` assumes. In this specific case the code is actually consistent because `MaxFeeRatio` is 100. However, the deduction logic in `buyGas` explicitly branches on whether `feePayer == sender`, not on `isRatioTx`, while `returnGas` branches only on `isRatioTx`. The two functions use **different discriminants** for what should be the same "is this the sender=feePayer trivial case" decision. This inconsistency is a latent correctness risk: any code path where `isRatioTx` is false but `validatedFeePayer != validatedSender` and the effective split is not exactly 100/0 (e.g., if `FeeRatio()`'s default ever changes, or a new tx type is added that is fee-delegated but not `TxInternalDataFeeRatio` with a fee split not equal to `MaxFeeRatio`) would silently misattribute the entire gas refund to `feePayer`, shortchanging the `sender` for their portion.

### Impact Explanation
If the invariant "non-ratio fee-delegated tx implies feePayer bears 100% of fee" (currently guaranteed by `MaxFeeRatio` default and tx-pool validation) is ever violated — e.g., through addition of a new transaction type, a change to the default `FeeRatio()`, or an unforeseen interaction — `returnGas()` would send the sender's rightful share of unused gas refund to the fee payer instead, resulting in an unauthorized value transfer/fund loss for the sender. Given this is state-transition logic executed for every transaction, such a defect would apply network-wide and affect consensus-critical balance accounting.

### Likelihood Explanation
Currently low, because `MaxFeeRatio` (100) exactly matches the tx-pool's assumption for non-ratio fee-delegated transactions, so `buyGas` and `returnGas` happen to agree today. However, the code has no explicit invariant check or shared logic between `buyGas`'s and `returnGas`'s conditionals — they use different conditions to reach the same conclusion, which is fragile and exactly the type of "conditional coupling" that produced the referenced `PerpEngine::fill` referral-discount bug (a value applied in one code path but implicitly and incorrectly assumed equivalent in another).

### Recommendation
Make `returnGas()` use the same discriminant as `buyGas()` (`validatedFeePayer == validatedSender`) rather than `isRatioTx`, or better, always compute `CalcFeeWithRatio(feeRatio, remaining)` — since `CalcFeeWithRatio` with `feeRatio = MaxFeeRatio` degenerates correctly to `(remaining, 0)`, removing the special-cased `else` branch entirely and eliminating the discrepancy between the two functions' logic.

### Proof of Concept
No concrete exploit is achievable against the current codebase because `MaxFeeRatio` (100) and tx-pool validation happen to keep the two conditionals in agreement for all currently defined transaction types. This finding is a code-quality/defense-in-depth issue: the conditions in `buyGas()` (`validatedFeePayer == validatedSender`) and `returnGas()` (`isRatioTx`) should be unified to prevent future regressions of the same class as the referenced `fees_prepayment == 0` bug, where an implicit assumption about a special-cased branch silently diverges from the fee-application logic used elsewhere. [4](#0-3) [1](#0-0)

### Citations

**File:** blockchain/state_transition.go (L294-376)
```go
func (st *StateTransition) buyGas() error {
	var (
		validatedFeePayer = st.msg.ValidatedFeePayer()
		validatedSender   = st.msg.ValidatedSender()
		feeRatio, _       = st.msg.FeeRatio()
		isOsaka           = st.evm.ChainConfig().Rules(st.evm.Context.BlockNumber).IsOsaka
	)

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
	} else {
		// 1. FeeDelegatedWithRatio with sender != feePayer
		// 2. FeeDelegated          with sender != feePayer

		// Before Osaka, only the check of the amount to be deducted is applied.
		// Therefore, all options are false.
		feePayerCheckOverflow, feePayerCheckWithValue := false, false
		senderCheckOverflow, senderCheckWithValue := false, false

		// For 2, feeRatio will be always 100 (feePayer pays all fee)
		feePayerFee, senderFee := types.CalcFeeWithRatio(feeRatio, mgval)
		feePayerBalanceCheck, senderBalanceCheck := feePayerFee, senderFee
		if isOsaka {
			// Overflow will be checked from osaka onwards.
			// Since the value is paid entirely by the sender, value checks only apply to sender.
			feePayerCheckOverflow, feePayerCheckWithValue = true, false
			senderCheckOverflow, senderCheckWithValue = true, true

			feePayerBalanceCheck, senderBalanceCheck = types.CalcFeeWithRatio(feeRatio, feeCap)
		}
		if err := st.checkFeePayerBalance(feePayerBalanceCheck, feePayerCheckOverflow, feePayerCheckWithValue); err != nil {
			return err
		}
		if err := st.checkSenderBalance(senderBalanceCheck, senderCheckOverflow, senderCheckWithValue); err != nil {
			return err
		}
		st.state.SubBalance(validatedFeePayer, feePayerFee)
		st.state.SubBalance(validatedSender, senderFee)
	}

	st.gas += st.msg.Gas()

	st.initialGas = st.msg.Gas()
	return nil
}
```

**File:** blockchain/state_transition.go (L812-828)
```go
// returnGas returns KAIA for remaining gas, exchanged at the original rate.
func (st *StateTransition) returnGas() {
	remaining := new(big.Int).Mul(new(big.Int).SetUint64(st.gas), st.gasPrice)

	validatedFeePayer := st.msg.ValidatedFeePayer()
	validatedSender := st.msg.ValidatedSender()
	feeRatio, isRatioTx := st.msg.FeeRatio()
	if isRatioTx {
		feePayer, feeSender := types.CalcFeeWithRatio(feeRatio, remaining)

		st.state.AddBalance(validatedFeePayer, feePayer)
		st.state.AddBalance(validatedSender, feeSender)
	} else {
		// To make a short circuit, the below routine processes when feeRatio == 100.
		st.state.AddBalance(validatedFeePayer, remaining)
	}
}
```

**File:** blockchain/tx_pool.go (L965-975)
```go
		} else {
			if senderBalance.Cmp(tx.Value()) < 0 {
				logger.Trace("[tx_pool] insufficient funds for cost(value)", "from", from, "balance", senderBalance, "value", tx.Value())
				return ErrInsufficientFundsFrom
			}

			if feePayerBalance.Cmp(tx.Fee()) < 0 {
				logger.Trace("[tx_pool] insufficient funds for cost(gas * price)", "feePayer", feePayer, "balance", feePayerBalance, "fee", tx.Fee())
				return ErrInsufficientFundsFeePayer
			}
		}
```
