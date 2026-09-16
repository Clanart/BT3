### Title
Gasless SwapTx admission trusts the pending ApproveTx's calldata amount instead of the resulting on-chain allowance - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`GaslessModule.checkBalanceForSwap` verifies `token.allowance(sender, router) >= amountIn` only when no ApproveTx is assumed to precede the SwapTx in the sender's nonce sequence. When an ApproveTx is assumed to precede (`swapNonce == senderNonce+1`), the module skips reading the authoritative on-chain `allowance()` value entirely and instead trusts the `amount` field decoded from the *pending, unexecuted* ApproveTx's calldata (checked later in `VerifyExecutable`'s SP2 condition). This mirrors the reported "Missing approval path" bug class: a security check is anchored to one specific call pattern (`approve(spender, amount)`) rather than to the canonical state (`allowance()`), so any divergence between "what the calldata says will happen" and "what the state actually becomes" is silently accepted.

### Finding Description
`checkBalanceForSwap` in <cite repo="bsaldua/kaia--019" path="kaiax/gasless/impl/tx_pool.go" start="144,151,153,154,156,160" end="162" /> computes:
```
senderNonce := g.getCurrentStateNonce(swapArgs.Sender)
noApproveTxPreceeds := swapNonce == senderNonce
if noApproveTxPreceeds {
    // only in this branch is on-chain allowance() actually queried
    approval, err := tokenContract.Allowance(nil, swapArgs.Sender, swapRouter)
    ...
}
```
When `noApproveTxPreceeds` is false (i.e., the SwapTx's nonce is `senderNonce+1`, meaning a not-yet-executed ApproveTx is assumed to occupy `senderNonce`), the real allowance check is skipped entirely. The only substitute check happens in `VerifyExecutable` at block-build time via `decodeApproveTx`/SP2: [1](#0-0) 
which compares `swapArgs.AmountIn` against the ApproveTx's **decoded calldata amount field**, not against the state that will actually result from executing that ApproveTx.

`IsApproveTx`/`decodeApproveTx` only recognize one exact call shape: a legacy tx whose first four bytes equal the selector for `approve(address,uint256)` [2](#0-1) , and further require the amount to equal `MaxUint256` [3](#0-2) . There is no re-validation of the resulting `allowance()` state after this specific ApproveTx executes — the whole mechanism assumes `approve(router, MaxUint256)` deterministically sets `allowance(sender, router) == MaxUint256` and nothing else in the same block can alter that outcome (e.g., a different, non-standard token behavior, a prior partial allowance combined with a non-overwriting `approve`, or any other mutation path). This is structurally identical to the original BaseVault issue: the approvals bookkeeping/validation is derived from a specific function-selector match on a *pending* mutation rather than confirmed post-state, so any allowance-affecting path not covered by that exact selector/branch is invisible to the check.

### Impact Explanation
The gasless flow lends KAIA to the sender via a `LendTx` computed from the anticipated `RepayAmount`, expecting the following `SwapTx` (which calls `swapForGas` → `transferFrom(sender, router, amountIn)`) to successfully pull the approved tokens and repay the proposer, per `repayAmount`/`lendAmount` in [4](#0-3) . If the assumption that the pending ApproveTx will produce sufficient real allowance is wrong at the moment the SwapTx actually executes, `transferFrom` inside `swapForGas` reverts, but the `LendTx` (a separate, already-committed transaction transferring KAIA to the sender) is not reversed. This is a fee-delegation/gasless-settlement theft scenario: the sender receives lent KAIA without the router ever collecting the promised token amount to repay it, because the admission-time check that is supposed to guarantee "approval will be sufficient" was based on unexecuted calldata rather than confirmed state.

### Likelihood Explanation
This is a medium-confidence, narrowly-reachable finding, not a fully demonstrated exploit, because:
- `allowedTokens` is populated from a system/governance-controlled Registry (`MultiCallGaslessInfo`) rather than being attacker-supplied, so an ordinary unprivileged sender cannot introduce an arbitrary non-standard ERC20 into the whitelist to intentionally break the `approve()`→`allowance()` determinism assumption.
- I was unable to fully verify (within available tool budget) whether `swapForGas`'s internal accounting or the block-builder's bundle-atomicity guarantees (LendTx + ApproveTx + SwapTx assembled together, per `GetLendTxGenerator`/`isApproveTxReady`/`isSwapTxReady` in `tx_pool.go`) actually prevent the LendTx from being included if the SwapTx later reverts. If bundle inclusion is all-or-nothing at the block-builder level, this specific path may already be mitigated in practice, and the residual risk is limited to whitelisted tokens with non-standard `approve` semantics — which is a lower-likelihood, governance-gated precondition.

### Recommendation
Remove the special-case branch that skips the on-chain `allowance()` check when an ApproveTx is assumed to precede. Always validate the actual resulting `allowance(sender, router)` (simulated against the state after applying the pending ApproveTx, or validated strictly at final block-assembly/execution time) rather than trusting the decoded calldata `amount` field as a proxy for the eventual on-chain state. Additionally, confirm and, if necessary, enforce true atomic bundling of LendTx + ApproveTx + SwapTx so that a SwapTx revert (for any reason, including allowance mismatches) also prevents/reverts the associated LendTx.

### Proof of Concept
Not independently reproduced. Conceptually: an attacker who can get a non-standard ERC20 token onto the `allowedTokens` whitelist (or exploit any behavior where `approve(router, MaxUint256)` does not deterministically result in `allowance(sender, router) == MaxUint256`) submits `ApproveTx(nonce=N)` followed by `SwapTx(nonce=N+1, amountIn=X)`. At mempool admission, `checkBalanceForSwap` skips the real `allowance()` check (since `noApproveTxPreceeds` is false) and only `VerifyExecutable`'s calldata-based SP2 check is applied. If the LendTx is issued based on this assumption and the SwapTx's `transferFrom` later fails or pulls less than expected due to the real allowance being insufficient, the sender retains the lent KAIA without full repayment.

### Citations

**File:** kaiax/gasless/impl/getter.go (L79-86)
```go
func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}
```

**File:** kaiax/gasless/impl/getter.go (L117-140)
```go
func decodeApproveTx(tx *types.Transaction, signer types.Signer) (*ApproveArgs, bool) {
	to, inputs, ok := decodeFunctionCall(tx, erc20ApproveFunc)
	if !ok {
		return nil, false
	}
	spender, ok := inputs["spender"].(common.Address)
	if !ok {
		return nil, false
	}
	amount, ok := inputs["amount"].(*big.Int)
	if !ok {
		return nil, false
	}
	from, err := types.Sender(signer, tx)
	if err != nil {
		return nil, false
	}
	return &ApproveArgs{
		Sender:  from,
		Token:   to,
		Spender: spender,
		Amount:  amount,
	}, true
}
```

**File:** kaiax/gasless/impl/getter.go (L242-245)
```go
		// SP2.
		if approveArgs.Amount.Cmp(swapArgs.AmountIn) < 0 {
			return fmt.Errorf("%w: approve amount %s, required amount %s", ErrInsufficientApproveAmount, approveArgs.Amount.String(), swapArgs.AmountIn.String())
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
