Only the compiled Go bindings for `GaslessSwapRouter` are indexed (`contracts/bindings/kip247/GaslessSwapRouter.go`); the Solidity source (`GaslessSwapRouter.sol`) is not present/indexed in this repo, so exact modifier/require statements (e.g. `onlyOwner`, hardcoded caps) cannot be cited directly from source — only inferred from the ABI/bindings and the constructor bytecode note ("Zero address is not allowed") and `Ownable` inheritance implied by `Owner()`/`TransferOwnership()` methods.

### Title
Owner front-running of `updateCommissionRate()` can reduce gasless-swap users' output below what was validated at submission - ([File: contracts/bindings/kip247/GaslessSwapRouter.go])

### Summary
`GaslessSwapRouter` exposes an owner-only `updateCommissionRate(uint256 _commissionRate)` function [1](#0-0) , with no visible upper bound and no timelock, mirroring the reported `setProtocolFee()` centralization pattern (arbitrary, immediate, unbounded fee change). This rate directly determines how much of a gasless user's swap output is retained as commission versus returned to the user in `swapForGas()` [2](#0-1) , which emits `SwappedForGas(proposer, amountRepaid, user, finalUserAmount, commission)` [3](#0-2) .

### Finding Description
Gasless users (unprivileged senders) submit an `approve` + `swapForGas` transaction pair through the Kaia gasless module. The node-side validation in `kaiax/gasless/impl/tx_pool.go` (`checkBalanceForSwap`) checks, at admission time, that `minAmountOut >= amountRepay` and that `amountIn >= gsr.GetAmountIn(minAmountOut)` using the router's *current* on-chain rate [4](#0-3) . The `RepayAmount` a user commits to is fixed off-chain and includes the lending fee that a proposer/relayer will be repaid from `finalUserAmount` [5](#0-4) .

Because `updateCommissionRate()` can be called by the router owner in the same block (or earlier) as a pending `swapForGas` transaction, with no rate cap and no delay, the owner can front-run a queued/pending gasless swap and raise the commission rate right before it executes. This increases the commission taken out of the swap output and reduces `finalUserAmount` actually delivered to the user, potentially below the `amountRepay` that was validated as sufficient when the transaction was accepted into the pool/gasless-tx-pair. This is structurally identical to the reported Teller `setProtocolFee()` issue: an owner can unilaterally and instantly change a fee parameter that a counterparty transaction relies on, with no bound or delay, enabling economic extraction from the counterparty (here, the gasless swap user / fee-delegation-like relationship between proposer and user).

### Impact Explanation
If commission is raised between mempool admission and execution, the user's realized `finalUserAmount` from `swapForGas()` can fall short of what was assumed sufficient to fund gas repayment (`amountRepay`), causing either (a) the gasless swap to under-deliver value to the user while the proposer/relayer's repayment claim on `finalUserAmount` proceeds, or (b) reverts/failures for the submitted transaction after the user already spent tokens in `amountIn`, redirecting value that should've gone to the user toward commission collected by the (centralized) owner. This is a value-extraction / fee-abuse risk directly analogous to the reported issue, reachable by any account participating in the gasless flow (an unprivileged transaction sender), without needing malicious peers, validators, or off-chain compromise.

### Likelihood Explanation
Likelihood is moderate-to-high in adversarial-owner scenarios: the update is a single onlyOwner transaction with no timelock, and Kaia validator/proposer nodes running the gasless module process `swapForGas` transactions from the pool, giving the owner clear visibility (via public mempool) of pending swaps and their `minAmountOut`/`amountRepay` values to compute a profitable front-run. There is no rate-limiting or bound-checking against the currently pending gasless transactions' assumptions.

### Recommendation
- Add a hard-coded maximum bound on `commissionRate` in `updateCommissionRate()`.
- Add a timelock/delay before rate changes take effect, so admitted gasless transactions in flight are unaffected.
- Alternatively, have `swapForGas()` snapshot/commit the commission rate the caller expected (e.g., pass a `maxCommissionRate` parameter and revert if the on-chain rate exceeds it), giving users slippage-style protection against fee changes analogous to `minAmountOut`.

### Proof of Concept
Conceptual PoC (source-level modifier/require checks in `GaslessSwapRouter.sol` were not available in this index to confirm exact revert conditions — flagged as unverified):
1. User submits `approve(router, amountIn)` then `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` with `minAmountOut == amountRepay`, validated by the gasless tx-pool checks [6](#0-5) .
2. Router owner observes the pending transaction and calls `updateCommissionRate(newHigherRate)` [1](#0-0)  in the same or an earlier block.
3. `swapForGas()` executes with the increased commission, reducing `finalUserAmount` below `amountRepay` [2](#0-1) , causing the user to receive less value than assumed valid at submission time while commission (subject to `CommissionClaimed`/owner withdrawal) increases.

**Note on index limitations**: because the `.sol` source for `GaslessSwapRouter` is not indexed, precise bound/require logic inside `updateCommissionRate()` and the exact interaction between `commissionRate` and `finalUserAmount` calculation could not be fully verified from source; a Devin session with full repository/file access would be needed to confirm exact code paths and any existing (unindexed) mitigations before finalizing severity.

### Citations

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L568-573)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactorSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L596-601)
```go
// UpdateCommissionRate is a paid mutator transaction binding the contract method 0x00fa3d50.
//
// Solidity: function updateCommissionRate(uint256 _commissionRate) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) UpdateCommissionRate(opts *bind.TransactOpts, _commissionRate *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "updateCommissionRate", _commissionRate)
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1206-1209)
```go
// ParseSwappedForGas is a log parse operation binding the contract event 0x60a11b162898ec58576fd25d00009d335193695470e7b3c4a5a34ec15ea71ddc.
//
// Solidity: event SwappedForGas(address indexed proposer, uint256 amountRepaid, address indexed user, uint256 finalUserAmount, uint256 commission)
func (_GaslessSwapRouter *GaslessSwapRouterFilterer) ParseSwappedForGas(log types.Log) (*GaslessSwapRouterSwappedForGas, error) {
```

**File:** kaiax/gasless/impl/tx_pool.go (L102-141)
```go
// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
	token := swapArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	g.gaslessInfoMu.RLock()
	swapRouter := g.swapRouter
	g.gaslessInfoMu.RUnlock()

	// tx.minAmountOut >= tx.amountRepay
	minAmountOut := swapArgs.MinAmountOut
	amountRepay := swapArgs.AmountRepay
	if minAmountOut.Cmp(amountRepay) < 0 {
		return fmt.Errorf("insufficient minAmountOut: minAmountOut=%s, amountRepay=%s", minAmountOut.String(), amountRepay.String())
	}

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(swapArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckSwapAmount() {
		// tx.amountIn >= gsr.getAmountIn(minAmountOut)
		routerContract, err := kip247.NewGaslessSwapRouterCaller(swapRouter, bc)
		if err != nil {
			return err
		}
		// Required token amountIn, given the current exchange rate and the declared minAmountOut.
		requiredAmountIn, err := routerContract.GetAmountIn(nil, token, minAmountOut)
		if err != nil {
			return err
		}
		if swapArgs.AmountIn.Cmp(requiredAmountIn) < 0 {
			return fmt.Errorf("insufficient amountIn: have=%s, want=%s", swapArgs.AmountIn.String(), requiredAmountIn.String())
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
