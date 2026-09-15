### Title
Gasless swap protections for the fee-lending proposer are enforced only at mempool admission (`GetCheckBalance`), not by consensus/EVM execution, allowing bypass and lender fund loss - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
This is a structural analog of the Vader `Pools.sol` finding: a protective calculation (there, IL protection; here, the guarantees that the block proposer who fronts gas for a `GaslessSwapTx` will be fully repaid) is performed by a *wrapper/gatekeeper layer* (`Router.removeLiquidity()` in the original; `GaslessModule.GetCheckBalance()` / `checkBalanceForSwap()` in kaia) rather than by the underlying value-moving function itself (`Pools.removeLiquidity()` in the original; the on-chain `GaslessSwapRouter.swapForGas()` call in kaia). Any path that reaches `swapForGas()` execution without going through this specific tx-pool admission check bypasses the protection, exactly as directly calling `Pools.removeLiquidity()` bypassed IL protection.

### Finding Description
The KIP-247 gasless flow works as follows: a block proposer lends gas to a user via a generated "lend" transaction, then the user's `GaslessSwapTx` (a call to `swapForGas` on the whitelisted `GaslessSwapRouter`) is expected to repay that lent amount (`amountRepay`) out of the swap proceeds.

The critical repayment-safety invariants — `minAmountOut >= amountRepay`, `amountIn >= gsr.getAmountIn(minAmountOut)` (i.e. the declared input is sufficient at the current price), sufficient ERC-20 allowance/balance, and `deadline >= currentTimestamp` — are implemented in `checkBalanceForSwap` in [1](#0-0) , which is only invoked from `GetCheckBalance()` [2](#0-1) . This function is wired into the transaction pool exclusively at the *admission* stage in `TxPool.validateTx`: [3](#0-2) 

Crucially, when a gasless tx's module recognizes it, `shouldSkipBalanceCheck = true` is set and the pool's normal `senderBalance.Cmp(tx.Cost())` balance check is skipped entirely, replaced solely by `checkBalance(tx)` — i.e., the gasless-specific checks are the *only* balance/repay-safety validation applied, and it happens once, at `AddLocal`/`AddRemote` time. There is no re-validation of these repayment invariants:
- when the tx pool re-orders/promotes transactions (`IsReady` in [4](#0-3)  only checks executability/ordering via `IsExecutable`/`VerifyExecutable`, which validates nonce sequencing and that `AmountRepay` matches the *computed* `repayAmount()`, but does not re-check current price/balance/allowance conditions that `checkBalanceForSwap` enforces),
- when the bundle is built for block inclusion (`ExtractTxBundles` in [5](#0-4) ),
- or during actual EVM execution of `swapForGas()` on-chain.

The on-chain `GaslessSwapRouter` contract (only available as a compiled/bound artifact, `contracts/bindings/kip247/GaslessSwapRouter.go`) is a black box in this index, but the integration test `tests/gasless_test.go` expects these exact rejections ("insufficient minAmountOut", "insufficient amountIn", "insufficient balance", "insufficient deadline", "sender with code is not allowed") to surface as errors from `sendSwapTx`/`SendTransaction` — i.e., from the local node's tx pool admission path, not necessarily as EVM reverts enforced by every node/path that can cause a `swapForGas` call to execute. [6](#0-5) 

Additionally, several of these checks are individually togglable per-node via `GaslessConfig` (`ShouldCheckSenderCode()`, `ShouldCheckSwapAmount()`, `ShouldCheckToken()`), meaning a proposer/CN with a different (looser) `GaslessConfig` — or `BalanceCheckLevelStatic`, as used to skip balance checks entirely in tests [7](#0-6)  — can admit and mine a `swapForGas` transaction that another node's stricter gatekeeper would have rejected, since the protection lives in the mempool layer of each individual node rather than being enforced uniformly by the EVM/consensus state-transition logic that all nodes must agree on.

### Impact Explanation
If the repayment-safety checks in `checkBalanceForSwap` are the sole enforcement point (rather than being additionally enforced on-chain, inside `swapForGas()`, in a way validated by every node during block execution), a malicious sender could:
- Submit a `GaslessSwapTx` through a proposer/CN configured with relaxed `GaslessConfig` checks (`ShouldCheckSwapAmount=false`, `ShouldCheckToken=false`, or a stale price snapshot), causing `swapForGas` to execute with insufficient `amountIn`/stale `minAmountOut` relative to the actual on-chain AMM price at execution time, or with `amountRepay` exceeding what the swap output can actually cover.
- The block proposer, who fronted `LendTxGenerator`'s gas via the lend transaction, then would not be fully repaid, resulting in direct loss of KAIA to the proposer (equivalent to the "unauthorized value movement / fee-delegation abuse" impact class), and inconsistent execution results/config divergence across CNs that could contribute to state or acceptance divergence between honest nodes running different `GaslessConfig` settings.

This mirrors the original Vader impact: protection intended for a designated flow (Router / lend-and-repay) can be bypassed by any path that reaches the underlying value-moving call without that flow's specific admission gate, harming the party the protection was designed to shield (LPs there; the gas-lending proposer here).

### Likelihood Explanation
Medium. Exploitation requires either (a) a proposer/CN running non-default `GaslessConfig` (which the config format explicitly allows to be toggled per-check, and is exercised in tests with `BalanceCheckLevelStatic` skipping checks), or (b) any code path that submits/executes a `swapForGas` transaction outside the standard `TxPool.validateTx`/`AddLocal`/`AddRemote` admission flow (e.g., a builder/relay directly assembling bundles or transactions for inclusion, bypassing pool admission). Given `GaslessConfig` is a per-node/CN operator-controlled setting and the checks are advisory mempool logic rather than protocol-enforced state-transition rules, this is readily reachable by any single unprivileged transaction sender targeting a CN with looser settings, without requiring any malicious-peer or consensus-message behavior.

### Recommendation
Move the repayment-safety invariants (`minAmountOut >= amountRepay`, sufficient `amountIn` relative to current price, sufficient token balance/allowance, deadline) into the `GaslessSwapRouter` contract's `swapForGas()` on-chain logic itself (enforced during EVM execution and thus uniformly validated by all nodes during block verification), rather than relying solely on the mempool-layer `checkBalanceForSwap`/`GetCheckBalance()` gate in `kaiax/gasless/impl/tx_pool.go`. If some of these checks must remain advisory/off-chain for gas-limit reasons, clearly document (in `kaiax/gasless/README.md`) that `GaslessConfig`'s per-check toggles are security-critical and must be uniformly configured cluster-wide, and consider making them non-optional (remove `ShouldCheckSwapAmount`/`ShouldCheckToken`/`ShouldCheckSenderCode` config flags) so that no CN can be misconfigured into skipping proposer-repayment protection.

### Proof of Concept
1. Configure a CN's `GaslessConfig` with `ShouldCheckSwapAmount() == false` and/or `BalanceCheckLevel == BalanceCheckLevelStatic` (a supported, tested configuration per `kaiax/gasless/impl/tx_pool_test.go:266-268`).
2. As an unprivileged sender, submit an `approve` + `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` tx pair where `amountIn` is deliberately less than `gsr.getAmountIn(minAmountOut)` at current AMM price (i.e., insufficient to guarantee `minAmountOut`), while keeping `minAmountOut >= amountRepay` nominally satisfied at stale/quoted prices.
3. Because `ShouldCheckSwapAmount()` is disabled on this CN, `checkBalanceForSwap` (kaiax/gasless/impl/tx_pool.go:128-142) skips the `amountIn >= requiredAmountIn` check, and the tx is admitted and bundled via `GetLendTxGenerator`/`ExtractTxBundles`.
4. If the underlying `GaslessSwapRouter.swapForGas()` contract does not itself independently re-validate this invariant on-chain (unverified in this index since only compiled bindings are available, not the Solidity source), the swap executes with actual output below `amountRepay`, and the proposer's `LendTxGenerator`-fronted gas is not fully repaid — a direct loss to the gas-lending proposer, functionally identical to a user bypassing IL protection by calling `Pools.removeLiquidity()` directly instead of through `Router.removeLiquidity()`.

*Note: I could not verify from the available index whether `GaslessSwapRouter.sol`'s actual Solidity source independently re-enforces these invariants on-chain (only the compiled Go bindings and bytecode are indexed, not the source). This is the key open question for confirming the severity of this finding — if the on-chain contract does redundantly enforce these checks, this reduces to a defense-in-depth/config-hygiene issue rather than an exploitable fund-loss bug.*

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

**File:** kaiax/gasless/impl/tx_pool.go (L185-230)
```go
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	tx, ok := txs[next]
	if !ok {
		return false
	}

	if !g.isReady(txs, next, ready) {
		return false
	}

	if g.IsBundleTx(tx) {
		// If prev tx is bundle tx, there's no need to check the knownTxs limit because it has been checked in the previous `IsReady()` execution.
		isPrevTxBundleTx := len(ready) != 0 && g.IsBundleTx(ready[len(ready)-1])
		if isPrevTxBundleTx {
			g.knownTxs.add(tx, TxStatusPending)
			return true
		}

		maxBundleTxsInPending := g.GetMaxBundleTxsInPending()
		if maxBundleTxsInPending != math.MaxUint64 {
			numExecutable := uint(g.knownTxs.numExecutable())

			numSeqTxs := uint(1)
			for i := next + 1; i < next+uint64(len(txs)); i++ {
				if tx, ok := txs[i]; ok && g.IsBundleTx(tx) {
					numSeqTxs++
				} else {
					break
				}
			}

			// false if there is possibility of exceeding max bundle tx num
			if numExecutable+numSeqTxs > maxBundleTxsInPending {
				logger.Trace("Not promoting a tx because of exceeding max bundle tx num", "tx", tx.Hash().String(), "numExecutable", numExecutable, "maxBundleTxsInPending", maxBundleTxsInPending)
				return false
			}
		}

		g.knownTxs.add(tx, TxStatusPending)
	}

	return true
}
```

**File:** blockchain/tx_pool.go (L918-932)
```go
	// If module recognizes the tx, run an alternative balance check and then skip the default balance check later.
	shouldSkipBalanceCheck := false
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			if checkBalance := module.GetCheckBalance(); checkBalance != nil {
				shouldSkipBalanceCheck = true
				err := checkBalance(tx)
				if err != nil {
					logger.Trace("[tx_pool] invalid funds of module transaction sender", "from", from, "txhash", tx.Hash().Hex())
					return err
				}
			}
			break
		}
	}
```

**File:** kaiax/gasless/impl/builder.go (L28-72)
```go
func (g *GaslessModule) ExtractTxBundles(txs []*types.Transaction, prevBundles []*builder.Bundle) []*builder.Bundle {
	// there are only at most two gasless transactions in pending for a sender
	bundles := []*builder.Bundle{}
	approveTxs := map[common.Address]*types.Transaction{}
	targetTxHash := common.Hash{}
	for _, tx := range txs {
		addr, err := types.Sender(g.signer, tx)
		if err != nil {
			continue
		}
		if g.IsApproveTx(tx) {
			approveTxs[addr] = tx
		} else if g.IsSwapTx(tx) && g.IsExecutable(approveTxs[addr], tx) {
			bundleTxs := builder.NewTxOrGenList(g.GetLendTxGenerator(approveTxs[addr], tx))
			if approveTxs[addr] != nil {
				bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(approveTxs[addr]))
			}
			bundleTxs = append(bundleTxs, builder.NewTxOrGenFromTx(tx))

			b := builder.NewBundle(
				bundleTxs,
				targetTxHash,
				false,
			)

			targetTxHash = tx.Hash()

			isConflict := false
			for _, prev := range append(prevBundles, bundles...) {
				if prev.IsConflict(b) {
					isConflict = true
					break
				}
			}
			if isConflict {
				// Gasless transactions will just fail even if they aren't bundled.
				continue
			}
			bundles = append(bundles, b)
		} else {
			targetTxHash = tx.Hash()
		}
	}
	return bundles
}
```

**File:** tests/gasless_test.go (L245-267)
```go
	//// Reject obviously reverting SwapTx.

	// reject swapTx when minAmountOut < amountRepay
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, common.Big0, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient minAmountOut")

	// reject swapTx when amountIn < router.GetAmountIn(minAmountOut)
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, common.Big0, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient amountIn")

	// reject swapTx when balance < amountIn
	// the test acc first received `transferToken` but used up some. So it has less than `transferToken`.
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, transferToken, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "insufficient balance")

	// reject swapTx when deadline is in the past
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, minAmountOut, amountRepaySwap, common.Big1)
	assert.ErrorContains(t, err, "insufficient deadline: deadline=1")

	// reject swapTx originating from an EOA with code
	sendSetCodeTx(t, chain, transactor, accounts[0])
	_, err = sendSwapTx(t, gsrContract, accounts[0], testTokenAddr, swapAmmount, minAmountOut, amountRepaySwap, deadline)
	assert.ErrorContains(t, err, "sender with code is not allowed")
```

**File:** kaiax/gasless/impl/tx_pool_test.go (L266-268)
```go
	// Skip balance check for this test
	gaslessConfig := *gasless.DefaultGaslessConfig()
	gaslessConfig.BalanceCheckLevel = gasless.BalanceCheckLevelStatic
```
