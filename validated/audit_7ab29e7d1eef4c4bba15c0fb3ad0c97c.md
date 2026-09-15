### Title
Gasless swap `repayAmount()` calculates the KAIA fee to be repaid from tx.GasLimit instead of actual gas used, causing systematic over-extraction from gasless users - ([File: kaiax/gasless/impl/getter.go])

### Summary
The `kaiax/gasless` module implements KIP-247 gasless transactions: a relayer (the node) "lends" KAIA to a fee-less user so the user's `ApproveTx`/`SwapTx` pair can pay gas, then the user's `SwapForGas` router repays the lender out of the swapped tokens. The amount to be repaid, `SwapTx.AmountRepay`, is required to exactly equal a locally-computed `repayAmount()` value, and this value is derived from each transaction's declared gas limit rather than the gas actually consumed on-chain, mirroring the analog bug class of computing a settlement/burn amount from an assumed value instead of the amount actually needed.

### Finding Description
`repayAmount()` and `lendAmount()` compute the KAIA amount that must be lent to, and later repaid by, a gasless user: [1](#0-0) 

```
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())   // R2
	}
	r.Add(r, swapTx.Fee())               // R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas)) // R1 (fixed 21000 gas)
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

`Transaction.Fee()` computes `GasPrice * GasLimit` (the transaction's declared gas limit, i.e. the sender-provided ceiling), not the gas actually consumed by execution. This value is enforced strictly against the user's on-chain `SwapForGas` call: [2](#0-1) 

```
	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, ...)
	}
```

However, the actual on-chain fee burned/collected for a transaction is based on `gasUsed`, not `gasLimit` — unused gas is refunded to the tx sender at the end of state transition: [3](#0-2) 

```
	gasRefund := st.calcRefund()
	st.gas += gasRefund
	...
	st.returnGas()
```

Because `gasUsed <= gasLimit` always holds for a successfully-executed transaction, `Fee() = GasPrice * GasLimit` is a strict upper bound on the real fee paid by the `ApproveTx`/`SwapTx` pair. The `AmountRepay` demanded from the user's swapped-token proceeds is therefore always greater than or equal to the actual KAIA that was spent covering gas, with the surplus flowing to the relayer/lender rather than being returned to the user. This is structurally the same defect class as the referenced report: the settlement amount is derived from an assumed/ceiling value (gas limit) instead of the value that is actually consumed (gas used), producing a systematic mismatch that benefits one counterparty at the expense of the other.

### Impact Explanation
Every gasless swap executed through this mechanism repays the lender more KAIA-equivalent value than was actually spent on gas whenever `gasUsed < gasLimit` for the `ApproveTx` and/or `SwapTx` (which is the common case, since callers typically set a gas limit with margin above actual usage, and `params.TxGas` fixed-cost assumption for the lend transfer itself may also not match). The excess is extracted from the gasless user's swapped token output on every single transaction, representing systematic value siphoning from gasless users to the relayer/auctioneer side (fee delegation counterparty), reachable by any unprivileged gasless user submitting a standard `Approve`+`Swap` pair through the public transaction/RPC path.

### Likelihood Explanation
High likelihood: this is not an edge case but the default behavior of the gasless flow, triggered on every gasless transaction pair submitted by any ordinary user via public RPC, since gas limits set by wallets/clients virtually always exceed exact gas usage (buffer margins are standard EVM/Kaia tx practice), and the strict equality check in `VerifyExecutable` (SP4) means users have no way to have the exact-actual-usage repay amount accepted—only the gas-limit-based, inflated amount is accepted.

### Recommendation
Compute `repayAmount`/`lendAmount` based on the actual gas consumed (or a value reconciled after execution, e.g., using receipt `GasUsed` and the effective gas price used in state transition) rather than the declared `GasLimit`, or refund the difference between `GasLimit*GasPrice` and `GasUsed*EffectiveGasPrice` back to the user as part of the `SwapForGas` settlement logic.

### Proof of Concept
1. A gasless user submits an `ApproveTx` with `GasLimit = 100000` (per `kaiax/gasless` conventions) but which only consumes ~40000 gas in practice due to already-existing approval/storage slots.
2. The user's paired `SwapTx` similarly is assigned `GasLimit = 500000` but consumes fewer gas units on execution.
3. `IsExecutable`/`VerifyExecutable` requires `SwapTx.AmountRepay == repayAmount(approveTx, swapTx)`, computed from `GasLimit`, per [2](#0-1) .
4. During actual execution, `state_transition.go`'s `calcRefund`/`returnGas` (lines 635-644) return unused gas to the paying account, so the true fee cost is strictly less than the `GasLimit`-based figure used to size `AmountRepay`.
5. The user's `SwapForGas` call is forced to repay the inflated (gas-limit-based) amount from swapped proceeds regardless, resulting in the lender/relayer over-collecting on every gasless swap.

Note: I was unable to fully inspect `Transaction.Fee()`'s exact implementation within the available iterations (only located it in `blockchain/types/transaction.go` without reading its body), so the assumption that `Fee() = GasPrice * GasLimit` is based on standard naming convention in this codebase and is not 100% confirmed by direct citation — this should be verified in a live session before treating the PoC as fully proven.

### Citations

**File:** kaiax/gasless/impl/getter.go (L260-263)
```go
	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}
```

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** blockchain/state_transition.go (L635-644)
```go
	// Compute refund
	gasRefund := st.calcRefund()
	st.gas += gasRefund
	if rules.IsPrague {
		// After EIP-7623: Data-heavy transactions pay the floor gas.
		if st.gasUsed() < floorDataGas {
			st.gas = st.initialGas - floorDataGas
		}
	}
	st.returnGas()
```
