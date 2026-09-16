### Title
Zero-fee GaslessSwapTx spam bypasses sender balance check in tx pool - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
Kaia's `blockchain/tx_pool.go` normally rejects any transaction whose sender cannot cover `tx.Cost()` (value + gasPrice*gasLimit). The `gasless` kaiax module overrides this default check for GaslessApproveTx/GaslessSwapTx via `TxPoolModule.GetCheckBalance()`, and the tx pool explicitly skips the normal KAIA-balance check for any tx recognized as a module tx.

### Finding Description
In `validateTx`, when a module (here, the gasless module) recognizes a tx via `IsModuleTx`, the pool sets `shouldSkipBalanceCheck = true` and runs the module-provided `checkBalance` instead of the sender-KAIA-balance comparison: [1](#0-0) [2](#0-1) 

For gasless transactions, `GetCheckBalance()` only validates ERC20 token-related conditions (token balance/allowance, swap amount vs. router quote, deadline, sender-has-code), never the sender's KAIA balance, because "the gas fee of gasless transaction's is funded by block proposer": [3](#0-2) [4](#0-3) [5](#0-4) 

The depth of the balance checks is configurable via `BalanceCheckLevel`, which defaults to `BalanceCheckLevelAll` but can be lowered by node operators to `BalanceCheckLevelStatic` (level 0), under which only the `minAmountOut >= amountRepay` and `deadline` static relations are checked — no on-chain token balance, allowance, or swap-amount call is made at all: [6](#0-5) 

An attacker who holds the minimum ERC20 token amount required to satisfy `checkBalanceForApprove`/`checkBalanceForSwap` (or who operates against a node configured with a reduced `BalanceCheckLevel`) can submit a very large volume of `GaslessSwapTx`/`GaslessApproveTx` transactions from many addresses **without needing to hold any KAIA at all**, whereas an ordinary (non-gasless) transaction would immediately be rejected by `ErrInsufficientFundsFrom` for the same lack of funds: [7](#0-6) 

Because entry into the pool (`AddLocal`/`AddRemote` → `validateTx`) costs the attacker nothing in KAIA, and each valid-looking pending gasless swap tx must be bundled by the block builder with a proposer-funded `LendTx` (`GetLendTxGenerator`) before block inclusion, an attacker can cheaply create load on tx-pool processing, `IsReady`/`isSwapTxReady` promotion logic, and the block-builder's `ExtractTxBundles` bundling logic, forcing proposers to repeatedly attempt (and discard) lend-tx generation and bundle-conflict checks for transactions that will ultimately fail deeper execution-time checks (e.g., `VerifyExecutable`) or simply expire: [8](#0-7) [9](#0-8) 

This is analogous to the reported "zero-weight extrinsic" class: functions/paths reachable by an unprivileged sender whose true resource cost is not covered by any fee paid by that sender, enabling cheap spam.

### Impact Explanation
The impact is bounded by module-level guardrails: `MaxBundleTxsInPending`/`MaxBundleTxsInQueue` (default 100/200) cap the number of concurrent gasless bundle txs, and `IsBundleTx`/`knownTxs` bookkeeping limits how many such txs occupy queue/pending slots at once: [10](#0-9) [11](#0-10) 

However, within those caps, the attacker consumes tx-pool/bundling/proposer CPU cycles (ERC20 `BalanceOf`/`Allowance`/`GetAmountIn` contract calls per tx, lend-tx generation, bundle conflict detection) while paying zero KAIA, which regular transactions cannot do. This is a Medium-severity resource/DoS-adjacent issue reachable purely from public RPC by any unprivileged account holding a negligible amount of an allowed token (or none, if `BalanceCheckLevel=Static`), not a direct fund-theft or supply-inflation bug.

### Likelihood Explanation
Likelihood is Medium: the attack path is reachable from any public RPC endpoint by simply crafting `approve`/`swapForGas` calldata against a whitelisted token/router, without needing validator or peer privileges, and does not require exploiting a coding defect beyond the intentional (but under-guarded) design decision to skip KAIA balance checks for gasless module txs. The severity is mitigated by the pending/queue caps but those caps are still shared, finite resources that can be monopolized by the attacker across many sender addresses (Sybil), since the caps are global counts, not per-sender.

### Recommendation
- Add a per-sender/per-IP rate limit or minimum-KAIA-stake requirement for submitting gasless transactions, independent of `BalanceCheckLevel`, so that a Sybil attacker cannot cheaply saturate `MaxBundleTxsInPending`/`MaxBundleTxsInQueue`.
- Consider requiring `BalanceCheckLevelAll` (or at minimum `TokenBalanceAndAllowance`) as a non-configurable floor rather than allowing operators to fully disable balance-related checks via `BalanceCheckLevelStatic`.
- Charge a small, non-refundable anti-spam cost (e.g., requiring the module tx to at least cover `PreAddTx`/pool-slot admission cost) or benchmark/limit the number of `BalanceOf`/`Allowance`/`GetAmountIn` EVM calls the tx pool performs per block/second for gasless-tx validation.

### Proof of Concept
1. Acquire a negligible balance of a token in `GaslessConfig.AllowedTokens` (or target a node running with `--gasless.balance-check-level=0`).
2. From N distinct addresses with zero KAIA balance, submit `GaslessApproveTx`/`GaslessSwapTx` pairs (`approve(spender, MaxUint256)` then `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`) satisfying `checkBalanceForApprove`/`checkBalanceForSwap`.
3. Observe that `validateTx` accepts these txs (`shouldSkipBalanceCheck = true` path) despite `senderBalance == 0`, whereas an equivalent non-gasless legacy tx from the same zero-balance sender is rejected with `ErrInsufficientFundsFrom`.
4. Repeat across many Sybil addresses up to `MaxBundleTxsInPending`/`MaxBundleTxsInQueue`, forcing the proposer's `ExtractTxBundles`/`GetLendTxGenerator` logic to repeatedly process bundles that consume CPU/RPC (ERC20 contract calls) without the attacker paying any fee.

### Citations

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

**File:** blockchain/tx_pool.go (L983-989)
```go
	} else if !shouldSkipBalanceCheck {
		// balance check for non-fee-delegated tx
		if senderBalance.Cmp(tx.Cost()) < 0 {
			logger.Trace("[tx_pool] insufficient funds for cost(gas * price + value)", "from", from, "balance", senderBalance, "cost", tx.Cost())
			return ErrInsufficientFundsFrom
		}
	}
```

**File:** kaiax/gasless/README.md (L9-27)
```markdown
Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.

### Transaction pool rules

#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.

#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```

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

**File:** kaiax/gasless/config.go (L55-100)
```go
	BalanceCheckLevelFlag = &cli.IntFlag{
		Name:     "gasless.balance-check-level",
		Usage:    "balance check level: 0=static checks, 1=token balance and allowance, 2=swap amount, 3=all",
		Value:    BalanceCheckLevelAll,
		Aliases:  []string{"kaiax.module.gasless.balance-check-level"},
		Category: "KAIAX",
	}
)

const (
	BalanceCheckLevelStatic                   = iota // relation between amounts and deadline
	BalanceCheckLevelTokenBalanceAndAllowance        // all above + token balance and allowance
	BalanceCheckLevelSwapAmount                      // all above +	amountIn calculated by dex
	BalanceCheckLevelAll                             // all above +	sender code check
)

type GaslessConfig struct {
	// all tokens are allowed if AllowedTokens is nil while all are disallowed if empty slice
	AllowedTokens         []common.Address `toml:",omitempty"`
	Disable               bool
	MaxBundleTxsInPending uint
	MaxBundleTxsInQueue   uint
	BalanceCheckLevel     int
}

func DefaultGaslessConfig() *GaslessConfig {
	return &GaslessConfig{
		AllowedTokens:         nil,
		Disable:               false,
		MaxBundleTxsInPending: 100,
		MaxBundleTxsInQueue:   200,
		BalanceCheckLevel:     BalanceCheckLevelAll,
	}
}

func (cfg *GaslessConfig) ShouldCheckToken() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelTokenBalanceAndAllowance
}

func (cfg *GaslessConfig) ShouldCheckSwapAmount() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelSwapAmount
}

func (cfg *GaslessConfig) ShouldCheckSenderCode() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelAll
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
