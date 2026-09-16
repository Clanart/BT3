### Title
Attacker can exhaust the global gasless bundle-tx queue capacity with disposable/worthless bundle transactions, causing denial-of-service for legitimate gasless users - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
The `kaiax/gasless` module enforces a single, global cap (`MaxBundleTxsInQueue`, default 200) on the number of gasless bundle transactions (`GaslessApproveTx`/`GaslessSwapTx`) that may sit in the tx pool queue at once. This cap is checked only in aggregate in `PreAddTx`, with no per-sender quota, mirroring the `DepositManagerV1` `TOKEN_ADDRESS_LIMIT` bug class where an attacker can fill a shared, globally-limited resource with worthless/disposable entries and deny legitimate users access to it.

### Finding Description
`PreAddTx` rejects a new bundle tx only when the aggregate `knownTxs.numQueue()` reaches `GetMaxBundleTxsInQueue()`: [1](#0-0) 

There is no restriction tying this counter to a specific sender or requiring meaningful economic value from the bundle tx — any transaction that satisfies `IsApproveTx`/`IsSwapTx` (an allowed token, correct spender/router, and `MaxUint256` approve amount, or a swap referencing an allowed token) counts as a "bundle tx" and consumes one of the limited queue slots: [2](#0-1) 

The default balance-check level (`BalanceCheckLevelAll`) only requires a nonzero token balance/allowance and that the sender has no code — it does not require any meaningful value or that the transaction ever actually executes profitably: [3](#0-2) 

An attacker who controls many disposable EOAs (cheap to fund with a small amount of KAIA for gas plus dust amounts of an allowed token) can repeatedly submit approve+swap pairs from these throwaway addresses. Since `MaxBundleTxsInQueue` (default 200) and `MaxBundleTxsInPending` (default 100) are configured as global caps rather than per-sender caps, this stream of low-value/never-meant-to-succeed transactions can occupy the entire queue capacity: [4](#0-3) 

Each occupying entry is only evicted after `QueueTimeout` (10 seconds) or `KnownTxTimeout` (30 seconds), so the attacker only needs to resubmit periodically to keep the queue saturated: [5](#0-4) 

Once the aggregate limit is reached, `PreAddTx` returns `ErrBundleTxQueueFull` for any newly submitted, legitimate gasless bundle transaction, exactly as the `DepositManagerV1` example rejects further deposits once `TOKEN_ADDRESS_LIMIT` is reached with attacker-supplied worthless ERC20s.

### Impact Explanation
This is a denial-of-service against the gasless transaction feature (KIP-247): legitimate users attempting gasless approve/swap transactions can have their bundle transactions permanently rejected from the pool queue as long as the attacker keeps refreshing their spam entries, because the shared capacity has no per-account fairness/reservation mechanism. This does not cause direct fund loss but denies the intended gas-sponsorship service to all users, which is the same class of "capacity exhaustion with worthless attacker-controlled entries" described in the reference report.

### Likelihood Explanation
The attack is cheap: it only requires funding several EOAs with minimal KAIA for gas and negligible token balances/approvals of any single allowed token (a token that is on the module's allow-list, not necessarily valuable), plus periodic resubmission before the queue/pending timeouts expire. No special privileges, validator/proposer access, or large capital is required — any public RPC caller can submit these transactions.

### Recommendation
Introduce a per-sender (or per-address-group) quota within the global `MaxBundleTxsInQueue`/`MaxBundleTxsInPending` limits so a single actor (or a small set of colluding addresses) cannot consume the entire shared capacity. Alternatively, require a minimum meaningful value threshold (e.g., minimum swap amount or token value) before a bundle tx is allowed to occupy a queue slot, and/or rate-limit bundle tx admission per sender address.

### Proof of Concept
1. Deploy/acquire N disposable EOAs, each funded with minimal KAIA for gas.
2. For each EOA, obtain a small balance of any token present in the gasless module's `allowedTokens` map (populated via `updateAddresses`), and issue an approve transaction (`GaslessApproveTx`) with `spender == swapRouter` and `amount == MaxUint256`, and a following `GaslessSwapTx` referencing the same allowed token — satisfying `isApproveTx`/`isSwapTx` in `kaiax/gasless/impl/getter.go`.
3. Submit these approve+swap pairs from all N EOAs concurrently to the public RPC/tx pool. Each pair is accepted by `PreAddTx` and added to `knownTxs` with `TxStatusQueue`, incrementing `numQueue()`.
4. Once `numQueue()` reaches `MaxBundleTxsInQueue` (default 200), subsequent legitimate gasless bundle transactions submitted by other users are rejected with `ErrBundleTxQueueFull` in `PreAddTx`.
5. Before `QueueTimeout` (10s)/`KnownTxTimeout` (30s) elapse, resubmit fresh approve+swap pairs from the same or new disposable EOAs to keep the queue saturated indefinitely, sustaining the denial-of-service against legitimate gasless users.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L32-53)
```go
var (
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)

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

**File:** kaiax/gasless/impl/tx_pool.go (L74-100)
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
}
```

**File:** kaiax/gasless/impl/getter.go (L69-103)
```go
// IsApproveTx checks following conditions:
// A1. tx.to is a whitelisted ERC20 token.
// A2. tx.data is `approve(spender, amount)`.
// A3. spender is a whitelisted SwapRouter contract.
// A4. amount is MaxUint.
func (g *GaslessModule) IsApproveTx(tx *types.Transaction) bool {
	args, ok := decodeApproveTx(tx, g.signer)
	return ok && g.isApproveTx(args)
}

func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}

// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
}
```

**File:** kaiax/gasless/config.go (L41-54)
```go
	MaxBundleTxsInPendingFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-pending",
		Usage:    "max number of gasless bundle txs in pending queue. Default value is 100. No limit if negative value",
		Value:    100,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-pending"},
		Category: "KAIAX",
	}
	MaxBundleTxsInQueueFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-queue",
		Usage:    "max number of gasless bundle txs in queue. Default value is 200. No limit if negative value",
		Value:    200,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-queue"},
		Category: "KAIAX",
	}
```
