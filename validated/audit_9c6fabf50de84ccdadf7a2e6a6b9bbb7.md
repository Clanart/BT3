### Title
Gasless swap "lend" transfer is unconditionally paid to the sender before the ERC20 balance/allowance/price checks are re‑validated at block‑inclusion time, allowing the sender to keep the proposer‑lent KAIA while causing the repayment leg to fail - (File: `kaiax/gasless/impl/getter.go`, `kaiax/gasless/impl/tx_pool.go`, `kaiax/gasless/impl/builder.go`)

### Summary
The `kaiax/gasless` module implements KIP‑247 gasless transactions: the block proposer "lends" KAIA to a user via an unconditional native-value `LendTx` [1](#0-0) , and the user is expected to repay that amount inside the subsequent `GaslessSwapTx` call to the whitelisted `GaslessSwapRouter` contract. The economic safety of this design depends on the swap actually succeeding on-chain and producing enough output to satisfy `swapForGas`'s internal repayment logic. The only place that checks whether the swap will actually succeed (token balance, allowance, and router exchange rate) is `checkBalanceForSwap`, which is invoked solely through `GetCheckBalance()` at tx‑pool admission/promotion time [2](#0-1) [3](#0-2) . When the bundle is actually assembled for block building, `ExtractTxBundles`/`IsExecutable`/`VerifyExecutable` only re‑check nonce ordering and the *arithmetic* correctness of `amountRepay` (`SP4`) — they do not re‑query the ERC20 token balance, allowance, or router exchange rate [4](#0-3) [5](#0-4) .

### Finding Description
The gasless bundle is `[LendTxGenerator, GaslessApproveTx(optional), GaslessSwapTx]` [6](#0-5) . `LendTx` is a plain native-KAIA transfer from the proposer to the sender with `Value = lendAmount(...)` computed purely from the transactions' declared gas fees [7](#0-6) , and it is signed and executed unconditionally — its success has no dependency on whether the following swap succeeds. The actual repayment of the lent KAIA happens inside the swap's on‑chain call to `swapForGas`, which will revert if the sender's ERC20 balance/allowance/exchange rate no longer satisfy the swap requirements.

The only pre-validation of those on-chain economic conditions (`tx.amountIn >= gsr.getAmountIn(minAmountOut)`, `token.allowance(sender, router) >= amountIn`, `token.balanceOf(sender) >= amountIn`) is `checkBalanceForSwap`, gated behind config flags `ShouldCheckSwapAmount()`/`ShouldCheckToken()` and invoked only via `GetCheckBalance()`, which is a tx-pool-level "balance check" hook — not re-executed when the transaction is finally selected for block inclusion in `ExtractTxBundles` [5](#0-4) . `IsExecutable`/`VerifyExecutable`, which *is* re-run at block-building time, only validates signer identity, token/spender whitelisting, nonce sequencing, and that `amountRepay` matches the formula `repayAmount()` — it performs no state queries against the token contract or the swap router's pricing [4](#0-3) .

Because a pending transaction can sit in the pool for a non‑trivial window before being picked up for block assembly (and the module's `PendingTimeout`/`QueueTimeout` allow up to 10 seconds, i.e., several blocks, before demotion [8](#0-7) ), an unprivileged sender can:
1. Submit a valid `GaslessApproveTx`/`GaslessSwapTx` pair that passes `checkBalanceForSwap` at admission time (sufficient token balance/allowance and a slippage-satisfying quote).
2. Before the bundle is selected for inclusion, transfer away the ERC20 tokens used as collateral (or otherwise move the market price so the router's `getAmountIn`/`getAmountOut` no longer support `minAmountOut`), causing `swapForGas` to revert on execution.
3. The `LendTx` — already an independent, unconditional value transfer — still executes and delivers the lent KAIA to the sender's own address, regardless of the swap's outcome, because Kaia bundle transactions are separate top-level transactions with their own state commit, not atomic sub-calls that roll back together.

The result is that the sender walks away with the proposer‑funded KAIA while the swap (and thus the repayment) fails, permanently draining value from the block proposer that lent it — directly analogous to the reported incident where an automated "lending"/vault mechanism (Agentic FoF) paid out funds that were never properly repaid due to a gap between pre-check and settlement.

### Impact Explanation
This is a concrete unauthorized value movement: KAIA is transferred out of the block proposer's balance to an attacker-controlled EOA with no enforced atomic repayment, because the economic pre-check (`checkBalanceForSwap`) that would prevent this is not re-validated at the point where the `LendTx` is irrevocably committed. Each successful griefing attempt costs the proposer the full `lendAmount` (approve tx fee + swap tx fee), and the attack is repeatable per nonce/approval cycle, enabling systematic draining of proposer funds through the gasless subsidy mechanism. This matches "Medium" severity fee/fee-delegation and lending-abuse impact criteria.

### Likelihood Explanation
The attack requires only standard, permissionless capabilities available to any transaction sender: crafting a legitimate-looking gasless approve/swap pair, having it admitted to the pool (passing the momentary balance/allowance/price checks), and then invalidating those conditions (e.g., moving tokens out, or trading against the router to move the price) before the proposer includes the bundle. No special privileges, validator/peer position, or contract deployment rights are needed — it is reachable purely via public RPC transaction submission, matching the "gasless user" and "unprivileged transaction sender" threat models explicitly in scope.

### Recommendation
Re-validate the economic preconditions of the swap (token balance, allowance, and router quote against `minAmountOut`/`amountRepay`) atomically at the point the bundle is selected for inclusion in `ExtractTxBundles`/`GetLendTxGenerator`, using the state as of the block being built, not only at initial tx-pool admission. Alternatively, make the `LendTx`'s payout state-conditional (e.g., have the swap router pull funds from the proposer only upon successful repayment inside the same top-level transaction, rather than pre-funding the sender via an independent unconditional transfer), and/or shorten/eliminate the window between admission checks and inclusion for gasless bundles.

### Proof of Concept
1. Attacker deploys/holds an allowed ERC20 token and obtains a router quote such that `AmountIn`/`MinAmountOut`/`AmountRepay` satisfy `checkBalanceForSwap` (balance ≥ AmountIn, allowance ≥ AmountIn, minAmountOut ≥ amountRepay, `getAmountIn(minAmountOut) ≤ AmountIn`).
2. Attacker submits `GaslessApproveTx` (nonce N) and `GaslessSwapTx` (nonce N+1) referencing that quote; both pass tx-pool admission via `GetCheckBalance()` [2](#0-1)  and are held in the pending pool per `IsReady`/`isSwapTxReady` [9](#0-8) .
3. Before the module's worker selects the bundle (`ExtractTxBundles` in `builder.go`), attacker transfers the ERC20 tokens (used as collateral) to another address they control, or otherwise moves the token/router exchange rate so `swapForGas` will revert on execution.
4. Since `ExtractTxBundles`/`IsExecutable` do not re-run `checkBalanceForSwap`, the bundle `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` is still built and included [5](#0-4) .
5. On execution: `LendTx` unconditionally transfers `lendAmount` KAIA from the proposer to the attacker's address; `GaslessApproveTx` may succeed trivially; `GaslessSwapTx` reverts inside `swapForGas` due to insufficient token balance/allowance/price, so no repayment occurs. The attacker keeps the lent KAIA at the proposer's expense.

*Note: I was unable to inspect the on-chain `GaslessSwapRouter.swapForGas` Solidity source in this index (only Go bindings were available) to confirm there is no independent, contract-level clawback of the lent amount on swap failure; if such a safety mechanism exists at the contract level, its effectiveness against the described TOCTOU gap should be independently verified.*

### Citations

**File:** kaiax/gasless/impl/getter.go (L211-266)
```go
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

**File:** kaiax/gasless/impl/tx_pool.go (L32-36)
```go
var (
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)
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

**File:** kaiax/gasless/impl/tx_pool.go (L107-182)
```go
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

**File:** kaiax/gasless/impl/tx_pool.go (L269-290)
```go
// isSwapTxReady assumes that the caller checked `g.IsSwapTx(swapTx)`
func (g *GaslessModule) isSwapTxReady(swapTx, prevTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, swapTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	var approveTx *types.Transaction
	if swapTx.Nonce() == nonce {
		approveTx = nil
	} else if swapTx.Nonce() == nonce+1 {
		if prevTx == nil || !g.IsApproveTx(prevTx) {
			return false
		}
		approveTx = prevTx
	} else {
		return false
	}

	return g.IsExecutable(approveTx, swapTx)
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

**File:** kaiax/gasless/README.md (L29-36)
```markdown
### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```
