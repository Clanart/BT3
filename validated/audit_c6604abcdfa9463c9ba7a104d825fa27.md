### Title
Unbounded GasLimit in Gasless Swap/Approve Transactions Allows Draining Proposer Funds via Inflated Lend Amount and EVM Gas Refunds - (File: kaiax/gasless/impl/getter.go)

### Summary
The `kaiax/gasless` module computes the amount of KAIA a block proposer must "lend" to a gasless-transaction sender purely from the sender's self-declared `GasPrice × GasLimit` on the (attacker-authored) approve/swap transactions, and never validates that the declared `GasLimit` is reasonable relative to the actual gas the call will consume. Because the EVM refunds unused gas allowance to the transaction sender, an attacker can declare an inflated `GasLimit`, receive a correspondingly inflated pre-funded loan from the proposer, and keep the unused portion as a gas refund — extracting value from the block proposer with no on-chain check tying the loan/repay figures to real execution cost.

### Finding Description
The gasless module builds a `LendTx` from the proposer's own key before executing the user's approve/swap pair, sized by `lendAmount()`: [1](#0-0) 
and the required repayment is computed with the matching `repayAmount()` formula, both derived only from `approveTxOrNil.Fee()` / `swapTx.Fee()` (i.e., `GasPrice * GasLimit` declared inside the attacker-signed transactions): [2](#0-1) 

These values feed the actual fund transfer executed as the first transaction of the bundle: [3](#0-2) 

Crucially, the gasless module explicitly bypasses the standard tx-pool balance check for these transactions: [4](#0-3) 
and its own balance/admission check (`checkBalanceForSwap`) validates token approvals/balances, `minAmountOut >= amountRepay`, and the swap deadline — but never validates that `GasLimit` is reasonable or bounded relative to the actual gas the approve/swap call will consume: [5](#0-4) 

Because the block's `LendTx` is generated for the *full declared* fee (`GasPrice × GasLimit`), and the EVM refunds any unused gas allowance back to the transaction sender at the same `GasPrice`, an attacker who declares an artificially large `GasLimit` on their approve/swap transaction receives a proportionally larger loan credited to their own account by the proposer, then only pays for the gas actually consumed during execution — pocketing the refunded difference. The equality check performed in `VerifyExecutable`/`IsExecutable` (`SP4`) only re-derives `repayAmount` from the same self-declared fee fields, so it cannot detect or prevent this — it validates internal consistency of attacker-controlled numbers, not their relation to real gas cost: [6](#0-5) 

This is directly analogous to the reported bug class of "functions accepting attacker-supplied values (e.g., extremely large values) with no bound check, disturbing internal administration" — here the unbounded input is `GasLimit` on a gasless swap/approve transaction pair submitted by any unprivileged sender, and the "internal administration" disturbed is the block proposer's KAIA balance used to fund gasless-transaction loans.

### Impact Explanation
Any unprivileged account (needing only enough of the whitelisted ERC-20 token balance to satisfy `checkBalanceForApprove`/`checkBalanceForSwap`, which do not depend on KAIA balance) can construct a gasless approve+swap transaction pair with an artificially inflated `GasLimit`. Once included in a block by the proposer, the proposer's own KAIA balance funds a `LendTx` sized to the inflated declared fee. After the swap executes using only the actual (lower) gas, the unused-gas EVM refund is paid to the attacker, resulting in a net, uncompensated KAIA transfer from the block proposer to the attacker for each such transaction — a concrete unauthorized value movement/fee-delegation abuse.

### Likelihood Explanation
The path is reachable from a single unprivileged, publicly-submittable transaction bundle (an approve tx + swap tx, or a lone swap tx) that satisfies the existing `IsApproveTx`/`IsSwapTx` pattern checks. No special privilege, node access, or validator collusion is required — only crafting a legacy transaction with an inflated `GasLimit` targeting the whitelisted swap router/token, which any RPC caller can submit to the mempool.

### Recommendation
Bound the `GasLimit` used in `lendAmount()`/`repayAmount()` to a conservative, protocol-enforced estimate of the actual gas the approve/swap call requires (e.g., via `eth_estimateGas`-style simulation or a fixed cap per call type), rather than trusting the attacker-declared `GasLimit` verbatim. Additionally, validate at pool-admission time (in `checkBalanceForApprove`/`checkBalanceForSwap`) that the declared `GasLimit` does not significantly exceed the expected gas cost of the corresponding approve/swap call, and consider reconciling `repayAmount` against actual gas used post-execution rather than the pre-declared fee.

### Proof of Concept
1. Attacker holds a small amount of a whitelisted gasless token but zero/near-zero KAIA balance.
2. Attacker crafts a legacy `GaslessApproveTx`/`GaslessSwapTx` pair (per `IsApproveTx`/`IsSwapTx` in `kaiax/gasless/impl/getter.go`) targeting the actual gas needed (e.g., ~100k gas) but sets `GasLimit` to a much larger value (e.g., 10,000,000) at a nonzero `GasPrice`.
3. Both transactions pass `checkBalanceForApprove`/`checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:74-182`) since those checks never inspect `GasLimit` reasonableness, and the standard KAIA balance check is skipped via `GetCheckBalance()` (`kaiax/gasless/impl/tx_pool.go:62-72`).
4. The block proposer includes the bundle; `GetLendTxGenerator` computes `lendAmount = approveTx.Fee() + swapTx.Fee()` using the inflated `GasLimit` (`kaiax/gasless/impl/getter.go:268-359`), funding the attacker's account with a correspondingly inflated KAIA credit before the swap executes.
5. During execution, only the actual (much smaller) gas is consumed; the EVM refunds the unused gas allowance (`GasLimit - gasUsed`) × `GasPrice` to the attacker's account — KAIA that was never truly needed and is not reclaimed by `repayAmount()`, which is derived from the same self-declared, unvalidated fee fields.

**Uncertainty note:** I was unable to inspect the Solidity source of `GaslessSwapRouter.sol` (only compiled Go bindings such as [7](#0-6)  are indexed), so I could not fully verify whether the on-chain `swapForGas` function independently reconciles `amountRepay` against actual gas consumption in a way that would neutralize this gap. If the user needs full certainty on the on-chain repayment logic, a Devin session with full repository/file access should inspect the actual `GaslessSwapRouter.sol` source.

### Citations

**File:** kaiax/gasless/impl/getter.go (L195-266)
```go
// IsGaslessPattern checks following conditions:
// Ax. IsApproveTx conditions (if ApproveTx != nil)
// Sx. IsSwapTx conditions
// AP1. ApproveTx.from == SwapTx.from
// SP1. ApproveTx.to == SwapTx.token
// SP2. ApproveTx.amount >= SwapTx.amountIn
// SP3. ApproveTx.nonce+1 == SwapTx.nonce and Gasless transactions are head for nonce
// SP4. SwapTx.amountRepay = RepayAmount(ApproveTx, SwapTx)
func (g *GaslessModule) IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool {
	err := g.VerifyExecutable(approveTxOrNil, swapTx)
	if err != nil {
		return false
	}
	return true
}

// VerifyExecutable checks if the given transactions form a valid gasless transaction
// It returns an error explaining why the transaction is not executable if it's not,
// and a boolean indicating whether the transaction is executable
func (g *GaslessModule) VerifyExecutable(approveTxOrNil, swapTx *types.Transaction) error {
	// Sx.
	swapArgs, ok := decodeSwapTx(swapTx, g.signer)
	if !ok {
		return ErrDecodeSwapTx
	}
	if !g.isSwapTx(swapArgs) {
		return ErrSwapTxInvalid
	}

	// Conditions involving ApproveTx
	if approveTxOrNil != nil {
		// Ax.
		approveArgs, ok := decodeApproveTx(approveTxOrNil, g.signer)
		if !ok {
			return ErrDecodeApproveTx
		}
		if !g.isApproveTx(approveArgs) {
			return ErrApproveTxInvalid
		}
		// AP1.
		if approveArgs.Sender != swapArgs.Sender {
			return ErrDifferentSenders
		}
		// SP1.
		if approveArgs.Token != swapArgs.Token {
			return fmt.Errorf("%w: approve token %s, swap token %s", ErrDifferentTokens, approveArgs.Token.Hex(), swapArgs.Token.Hex())
		}
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
		}
		// SP3.
		if approveTxOrNil.Nonce()+1 != swapTx.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, swap nonce %d (expected %d)", ErrNonSequentialNonce, approveTxOrNil.Nonce(), swapTx.Nonce(), approveTxOrNil.Nonce()+1)
		}
		if nonce := g.getCurrentStateNonce(approveArgs.Sender); nonce != approveTxOrNil.Nonce() {
			return fmt.Errorf("%w: approve nonce %d, current nonce %d", ErrApproveNonceNotCurrent, approveTxOrNil.Nonce(), nonce)
		}
	} else {
		// SP3.
		if nonce := g.getCurrentStateNonce(swapArgs.Sender); nonce != swapTx.Nonce() {
			return fmt.Errorf("%w: swap nonce %d, current nonce %d", ErrSwapNonceNotCurrent, swapTx.Nonce(), nonce)
		}
	}

	// SP4.
	if swapArgs.AmountRepay.Cmp(repayAmount(approveTxOrNil, swapTx)) != 0 {
		return fmt.Errorf("%w: got %s, expected %s", ErrIncorrectRepayAmount, swapArgs.AmountRepay.String(), repayAmount(approveTxOrNil, swapTx).String())
	}

	return nil
}
```

**File:** kaiax/gasless/impl/getter.go (L268-313)
```go
// MakeLendTx creates a transaction with following properties:
// L1. LendTx.type = 0x7802 (TxTypeEthereumDynamicFee)
// L2. LendTx.from = proposer
// L3. LendTx.to = SwapTx.from
// L4. LendTx.value = LendAmount(approveTxOrNil, swapTx)
func (g *GaslessModule) GetLendTxGenerator(approveTxOrNil, swapTx *types.Transaction) *builder.TxOrGen {
	var src []byte
	if approveTxOrNil != nil {
		src = append(src, approveTxOrNil.Hash().Bytes()...)
	}
	src = append(src, swapTx.Hash().Bytes()...)
	bundleHash := crypto.Keccak256Hash(src)

	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId = g.InitOpts.ChainConfig.ChainID
			signer  = types.LatestSignerForChainID(chainId)
			key     = g.InitOpts.NodeKey
		)

		to, err := types.Sender(signer, swapTx)
		if err != nil {
			return nil, err
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &to,
			types.TxValueKeyAmount:     lendAmount(approveTxOrNil, swapTx),
			types.TxValueKeyData:       common.Hex2Bytes("0x"),
			types.TxValueKeyGasLimit:   params.TxGas,
			types.TxValueKeyGasFeeCap:  swapTx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  swapTx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)
		return tx, err
	}

	return builder.NewTxOrGenFromGen(gen, bundleHash)
}
```

**File:** kaiax/gasless/impl/getter.go (L346-359)
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
```

**File:** kaiax/gasless/impl/getter.go (L361-367)
```go
func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L102-182)
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
	}

	if g.GaslessConfig.ShouldCheckToken() {

		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// If SwapTx.nonce is the sender's next nonce, then there is no room for ApproveTx proceeding SwapTx.
		senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
		noApproveTxPreceeds := swapNonce == senderNonce
		if noApproveTxPreceeds {
			// tx.token.allowance(sender, router) >= tx.amountIn
			approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
			if err != nil {
				return err
			}
			if approval.Cmp(swapArgs.AmountIn) < 0 {
				return fmt.Errorf("insufficient approval: approval=%s, want=%s", approval.String(), swapArgs.AmountIn.String())
			}
		}

		// tx.token.balanceOf(sender) >= tx.amountIn
		balance, err := tokenContract.BalanceOf(nil, swapArgs.Sender)
		if err != nil {
			return err
		}
		if balance.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("insufficient balance: balance=%s, want=%s", balance.String(), swapArgs.AmountIn.String())
		}
	}

	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}

	return nil
}
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-566)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}

// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterSession) SwapForGas(token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.Contract.SwapForGas(&_GaslessSwapRouter.TransactOpts, token, amountIn, minAmountOut, amountRepay, deadline)
}
```
