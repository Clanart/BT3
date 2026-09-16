### Title
Gasless swap deadline is only enforced as a soft mempool policy, not at consensus/state-transition time, allowing a stale `SwapForGas` to be included and executed unfavorably - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Kaia gasless module lets an unprivileged sender submit an ERC-20 `approve` + `SwapForGas` transaction pair so their swap fee/gas is repaid out of the swapped tokens. The `deadline` field of the swap is checked only inside `checkBalanceForSwap`, which is wired into the tx pool's `GetCheckBalance` callback used during pool admission/promotion. This is analogous to the reported Uniswap issue: a low-level, unprotected execution path exists where a stale trade parameterization can be executed long after intended, because the "deadline" gate is not part of consensus-level transaction validation.

### Finding Description
`checkBalanceForSwap` explicitly performs the deadline check as one of several off-chain heuristics: [1](#0-0) [2](#0-1) 

This function is only invoked from `GetCheckBalance`, which is registered as a tx-pool balance-check hook, not as part of block execution: [3](#0-2) 

When a block is actually processed — either by the proposer's `Execute`/`ExecuteTransactions` path or by other validators replaying the block — transactions are run through `ApplyTransaction`, which only performs nonce/signature/balance/gas validation and then calls into the EVM; there is no call to `checkBalanceForSwap` or any deadline check in this path: [4](#0-3) [5](#0-4) 

No `.sol` source with a `deadline` check inside the on-chain `GaslessSwapRouter`/`swapForGas` implementation could be located in the codebase index, and the swap function signature only exposes `deadline` as one of many ABI-encoded integer parameters passed through `contract.Transact`, without any indication of an on-chain enforcement in the generated bindings: [6](#0-5) 

Because the deadline gate lives exclusively in the tx pool module (a soft, node-local admission/promotion policy), it can be bypassed by any path that inserts the transaction into a block without going through the pool's `GetCheckBalance` filter (e.g., a block proposer/builder directly incorporating a previously valid but now-expired signed `SwapForGas` transaction, or a validator/CN operating with a different or disabled gasless config). Once included, `ApplyTransaction`/`ExecuteTransactions` will execute the swap unconditionally as long as the underlying EVM call succeeds, with no re-validation of `deadline` at the state-transition layer.

### Impact Explanation
This breaks the "deadline" invariant users rely on to bound the time window in which their swap-and-repay can execute. Because the exchange-rate/`minAmountOut` bound was computed once at signing/admission time against then-current pool state, executing the trade much later (after the deadline) can force the gasless sender into an unfavorable market price while their gas/fee is still repaid via `GetLendTxGenerator`'s reconciliation. Since only the tx-pool layer enforces the deadline, and that check is not part of consensus-verified state transition, differently-configured or malicious block builders could still include and successfully execute an expired swap, causing value loss for an unprivileged gasless-tx sender and enabling MEV extraction by whoever controls block assembly at that moment.

### Likelihood Explanation
Reachable directly by any unprivileged gasless-swap sender or by a block proposer with no special privilege beyond normal block-building rights (which is explicitly in-scope per the "gasless" module note). The check is trivially bypassable because it is implemented purely as a tx-pool heuristic (`GetCheckBalance`/`checkBalanceForSwap`) rather than as an EVM-level or state-transition-level assertion, so it requires only that a proposer/builder include an already-signed, deadline-expired `SwapForGas` tx directly in a block rather than relying on the standard pool promotion path.

### Recommendation
Enforce the `deadline` check inside the on-chain `swapForGas` (or equivalent) contract logic itself, so it is validated during `ApplyTransaction`/EVM execution and thus by every node during block validation and replay — not only during local tx-pool admission. This mirrors Uniswap's own fix of moving deadline checks into the contract call path (`checkDeadline` modifier) rather than relying on caller-side/off-chain policy.

### Proof of Concept
1. A gasless user signs `SwapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` with `deadline = now + 300`.
2. The tx pool's `checkBalanceForSwap` validates it (deadline satisfied at admission time) and admits it as pending, per `kaiax/gasless/impl/tx_pool.go` lines 175-182.
3. Instead of relying on ordinary promotion, a block proposer/builder holds and later inserts this same signed transaction directly into a block body after `deadline` has passed (bypassing another `GetCheckBalance` re-check, e.g., via a different node/build path).
4. During block execution, `ApplyTransaction` (blockchain/blockchain.go) and `ExecuteTransactions` (work/execution.go) simply call into the EVM without re-checking `deadline`; the swap executes against current (possibly drastically different) market conditions, and `amountRepay` is still extracted from the user via the lend-tx flow, unfavorably to the original signer's intent.

Note: I could not directly inspect the Solidity source of `GaslessSwapRouter`/`swapForGas` (only Go bindings were indexed), so I cannot definitively confirm the on-chain contract has zero deadline enforcement; this is inferred from the absence of any `deadline` reference in indexed `.sol` files and the binding signature showing `deadline` as a plain parameter with no explicit modifier semantics. A Devin session with full repository access would be needed to confirm the exact on-chain contract behavior.

### Citations

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

**File:** kaiax/gasless/impl/tx_pool.go (L102-107)
```go
// tx.minAmountOut >= tx.amountRepay
// tx.amountIn >= gsr.getAmountIn(minAmountOut)
// tx.token.approval(sender, router) >= tx.amountIn
// tx.token.balanceOf(sender) >= tx.amountIn
// tx.deadline >= currentTimestamp
func (g *GaslessModule) checkBalanceForSwap(swapArgs *SwapArgs, swapNonce uint64) error {
```

**File:** kaiax/gasless/impl/tx_pool.go (L175-182)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
	}

	return nil
}
```

**File:** blockchain/blockchain.go (L2802-2834)
```go
	blockNumber := header.Number.Uint64()
	signer := types.MakeSigner(chainConfig, header.Number)

	// validation for each transaction before execution
	if err := tx.Validate(statedb, signer, blockNumber, false); err != nil {
		return nil, nil, err
	}

	msg, err := tx.AsMessageWithAccountKeyPicker(signer, statedb, blockNumber)
	if err != nil {
		return nil, nil, err
	}
	// Create a new context to be used in the EVM environment
	blockContext := NewEVMBlockContext(header, bc, author)
	txContext := NewEVMTxContext(msg, header, chainConfig)
	// Create a new environment which holds all relevant information
	// about the transaction and calling mechanisms.
	vmenv := vm.NewEVM(blockContext, txContext, statedb, chainConfig, vmConfig)

	// EEST test hook: validator-only extension point.
	if bc != nil {
		if v, hasMethod := bc.Validator().(interface {
			BeforeApplyMessage(*vm.EVM, *types.Transaction)
		}); hasMethod {
			v.BeforeApplyMessage(vmenv, msg)
		}
	}

	// Apply the transaction to the current state (included in the env)
	result, err := ApplyMessage(vmenv, msg)
	if err != nil {
		return nil, nil, err
	}
```

**File:** work/execution.go (L147-188)
```go
func (e *DefaultExecutor) ExecuteTransactions(txs []*types.Transaction) (*consensus.ExecutionResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()

	if !e.initialized {
		return nil, ErrExecutorNotInitialized
	}

	// Pre-tx hooks (EIP-2935, kaiax module pre-hooks, etc.)
	e.chain.Processor().InitializeState(e.header, e.state)

	// Extract the block proposer from the header seal — must match
	// StateProcessor.Process so COINBASE and reward distribution are identical.
	// Using e.nodeAddr here would be wrong: the local validator is not
	// necessarily the proposer of this block.
	author, _ := e.chain.Sealer().Author(e.header)

	var (
		receipts types.Receipts
		allLogs  []*types.Log
		usedGas  = new(uint64)
	)

	for i, tx := range txs {
		e.state.SetTxContext(tx.Hash(), common.Hash{}, i)
		receipt, _, err := e.chain.ApplyTransaction(
			e.config, &author, e.state, e.header, tx, usedGas, &vm.Config{},
		)
		if err != nil {
			return nil, fmt.Errorf("tx %d (%s): %w", i, tx.Hash().Hex(), err)
		}
		receipts = append(receipts, receipt)
		allLogs = append(allLogs, receipt.Logs...)
	}

	e.header.GasUsed = *usedGas
	e.txs = txs
	e.receipts = receipts
	e.logs = allLogs
	e.usedGas = *usedGas

	return e.buildResult(), nil
```

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L554-559)
```go
// SwapForGas is a paid mutator transaction binding the contract method 0x80426901.
//
// Solidity: function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) returns()
func (_GaslessSwapRouter *GaslessSwapRouterTransactor) SwapForGas(opts *bind.TransactOpts, token common.Address, amountIn *big.Int, minAmountOut *big.Int, amountRepay *big.Int, deadline *big.Int) (*types.Transaction, error) {
	return _GaslessSwapRouter.contract.Transact(opts, "swapForGas", token, amountIn, minAmountOut, amountRepay, deadline)
}
```
