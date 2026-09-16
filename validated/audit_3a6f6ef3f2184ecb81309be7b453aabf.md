Based on my investigation, I found a strong structural analog to the reported bug class in the gasless module's transaction pool admission logic.

### Title
Unprivileged senders can exhaust the global gasless bundle-tx queue limit before balance/allowance validation, blocking legitimate GaslessTx senders - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `disperser` report describes a pattern where a global, shared resource is consumed by a request before that request is actually authorized/validated, allowing an unprivileged caller to starve legitimate users of that shared resource. The `kaiax/gasless` module's `PreAddTx` function exhibits the same structural pattern: it admits any transaction that merely decodes as a `GaslessApproveTx`/`GaslessSwapTx` into a shared, capacity-limited queue (`MaxBundleTxsInQueue`) before any balance, allowance, or token-legitimacy check is performed.

### Finding Description
`PreAddTx` is the tx-pool admission hook for the gasless module. It checks only that the tx is a recognized `IsBundleTx` shape and that it is not already known, then immediately reserves a slot in the shared `knownTxs` queue against the global `MaxBundleTxsInQueue` limit: [1](#0-0) 

The actual economic/legitimacy checks — `checkBalanceForApprove` and `checkBalanceForSwap` — live in `GetCheckBalance()`, a separate function exposed to the pool: [2](#0-1) 

Critically, the token used in these balance checks is attacker-controlled data taken directly from the transaction's `ApproveArgs.Token` / `SwapArgs.Token` fields, not a fixed, protocol-approved token: [3](#0-2) 

Because the token address is attacker-chosen, an attacker can trivially satisfy `tokenBalance.Sign() <= 0` checks by deploying a throwaway ERC20 contract and minting balance to itself, or by using distinct sender addresses. Combined with the fact that `MaxBundleTxsInQueue` is a single global counter (not per-account), this lets any unprivileged EOA submit many syntactically valid `GaslessApproveTx`/`GaslessSwapTx` transactions — each cheaply satisfying `IsModuleTx`/`IsBundleTx` — to fill `g.knownTxs.numQueue()` up to the configured cap, causing `ErrBundleTxQueueFull` for all subsequent (including legitimate) gasless senders: [4](#0-3) [5](#0-4) 

This mirrors the disperser bug: a global capacity counter is consumed based on a cheap-to-forge validity check, before/independent of the check that would actually reject an illegitimate sender.

### Impact Explanation
This is a denial-of-service against the gasless (KIP-247) subsystem: legitimate users who want to use gasless approve/swap transactions (funded by the block proposer's `LendTxGenerator`) can be locked out because the shared queue capacity is exhausted by junk bundle transactions from unrelated, unprivileged senders. Since gasless transactions are meant to lower the barrier to entry for fee-less swaps, this directly undermines the availability guarantee of that feature for all users on the node, not just a single victim account.

### Likelihood Explanation
Likelihood is high for any node running with a finite `MaxBundleTxsInQueue` (the default is finite; the test suite explicitly exercises the finite-limit-enforced path): [6](#0-5) 
An attacker only needs to be able to submit ordinary transactions and optionally deploy a cheap ERC20 contract — both are unprivileged, permissionless actions reachable from any RPC caller.

### Recommendation
1. Move balance/allowance/token legitimacy checks (`GetCheckBalance`) ahead of, or fused into, `PreAddTx` so that the global queue slot is only reserved for transactions that have already passed a cheap sanity check.
2. Apply per-sender or per-token quotas within the bundle-tx queue rather than a single global counter, so that one address (or one attacker-controlled token) cannot consume the entire shared capacity.
3. Consider requiring the approved/swapped token to be from an allow-list (e.g., pairs registered with the `GaslessSwapRouter`) before a transaction is allowed to occupy a queue slot, closing the "attacker deploys own ERC20" bypass of `checkBalanceForApprove`/`checkBalanceForSwap`.

### Proof of Concept
1. Attacker deploys N throwaway ERC20 tokens (or reuses one) and mints a nonzero balance to N distinct funded EOAs.
2. Each EOA submits a `GaslessApproveTx` (or matching `GaslessApproveTx`+`GaslessSwapTx` pair) referencing the throwaway token, satisfying `IsModuleTx` and the cheap `tokenBalance.Sign() > 0` check in `checkBalanceForApprove`.
2. Repeat until `g.knownTxs.numQueue() >= MaxBundleTxsInQueue` (per `PreAddTx`), after which `ErrBundleTxQueueFull` is returned for all further gasless transactions, including from legitimate users, until the attacker's junk transactions time out (`QueueTimeout`) — at which point the attacker can repeat the flood.

Note: I was unable to fully confirm, within the available tool budget, the exact call order between `PreAddTx` and `GetCheckBalance` inside `blockchain/tx_pool.go` (both are referenced there), so it is possible the pool invokes `GetCheckBalance` before `PreAddTx` in some code paths. Since the token in the balance check is attacker-controlled regardless of ordering, the queue-exhaustion vector holds either way, but confirming the precise sequencing would benefit from further review of `blockchain/tx_pool.go`.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L38-53)
```go
func (g *GaslessModule) PreAddTx(tx *types.Transaction, local bool) error {
	g.knownTxsMu.RLock()
	defer g.knownTxsMu.RUnlock()

	if knownTx, ok := g.knownTxs.get(tx.Hash()); ok && knownTx.elapsedPromotedOrAddedTime() < KnownTxTimeout {
		return ErrUnableToAddKnownBundleTx
	}

	if g.IsBundleTx(tx) {
		if uint(g.knownTxs.numQueue()) >= g.GetMaxBundleTxsInQueue() {
			return ErrBundleTxQueueFull
		}
		g.knownTxs.add(tx, TxStatusQueue)
	}
	return nil
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

**File:** kaiax/gasless/impl/tx_pool.go (L74-99)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
```

**File:** kaiax/gasless/impl/errors.go (L38-39)
```go
	ErrUnableToAddKnownBundleTx  = errors.New("cannot add known bundle tx during cooldown")
	ErrBundleTxQueueFull         = errors.New("bundle tx queue is full")
```

**File:** kaiax/gasless/impl/tx_pool_test.go (L121-125)
```go
	t.Run("finite limit still enforced", func(t *testing.T) {
		g := newModule(1)
		require.NoError(t, g.PreAddTx(bundleTx(), false))
		require.ErrorIs(t, g.PreAddTx(bundleTx(), false), ErrBundleTxQueueFull)
	})
```
