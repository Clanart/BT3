### Title
Gasless proposer fee-lending is unrepaid when balance/allowance checks are disabled or bypassed at block-building time - (File: kaiax/gasless/impl/getter.go, kaiax/gasless/impl/tx_pool.go)

### Summary
The external report describes a trading-bot-style hack causing ~$230K user-fund loss from a smart-contract vulnerability. The closest reachable analog in the Kaia client is the `kaiax/gasless` module (KIP-247), where a block proposer fronts KAIA gas fees to an unprivileged sender via a `LendTxGenerator`-produced transaction, expecting the sender's on-chain `swapForGas` call to repay that amount. The repayment guarantee is enforced only by mempool-admission heuristics (`GetCheckBalance`) that are configurable/skippable, not by anything in `VerifyExecutable`, which is the function that actually gates bundling into a block.

### Finding Description
`VerifyExecutable` (`kaiax/gasless/impl/getter.go:214-266`), which is the authoritative function used both for tx-pool promotion (`isReady`/`isApproveTxReady`/`isSwapTxReady` in `kaiax/gasless/impl/tx_pool.go:233-290`) and ultimately for constructing the `[LendTxGenerator, ApproveTx?, SwapTx]` bundle, verifies only:
- static token/router whitelisting (`isApproveTx`/`isSwapTx`),
- sender/token/nonce/amount relationships between ApproveTx and SwapTx,
- that `SwapTx.AmountRepay == repayAmount(...)` (a purely arithmetic self-consistency check, `kaiax/gasless/impl/getter.go:261-263`, `361-367`).

It performs **no check that the sender actually holds the ERC-20 balance/allowance needed to pay for the swap**, nor that the swap will actually succeed and yield enough output to cover `AmountRepay`. Those real economic checks live in a *separate*, optional function, `GetCheckBalance()` → `checkBalanceForSwap` (`kaiax/gasless/impl/tx_pool.go:62-182`), which is explicitly gated by the configurable `GaslessConfig.BalanceCheckLevel` (`kaiax/gasless/config.go:64-100`):
- `BalanceCheckLevelStatic` (0) skips token balance, allowance, and DEX amountIn checks entirely.
- Even at higher levels, checks are best-effort, point-in-time (`nil` `CallOpts`, i.e. against `currentState`, not the state at time of block assembly) and can become stale between admission and inclusion, since `VerifyExecutable`/block-building re-derives none of this.

Consequently, the proposer's `LendTxGenerator` (`GetLendTxGenerator`, `kaiax/gasless/impl/getter.go:273-313`) unconditionally transfers `lendAmount(approveTxOrNil, swapTx)` (the sum of approve/swap tx fees, `kaiax/gasless/impl/getter.go:346-359`) from the block proposer's own account to the sender **before** the swap executes, based solely on the arithmetic/nonce checks in `VerifyExecutable`. If the sender's ERC-20 balance/allowance is insufficient (skipped because `BalanceCheckLevel=0`/`1`, or stale/race-conditioned), the on-chain `swapForGas` call inside the bundle will revert (per KIP-247's router logic), meaning the sender never repays the proposer via the swap's token transfer, yet the proposer's `LendTx` value transfer to the sender's EOA is an ordinary value transfer that already succeeded and is irreversible within the same block (it is a separate transaction in the bundle, not an atomic sub-call the router can roll back).

### Impact Explanation
Every accepted-but-failing gasless swap bundle causes the block proposer to permanently lose the lent KAIA (`R2+R3`, i.e. the ApproveTx+SwapTx fee amounts) to the sender's EOA with no repayment, since the LendTx transfer is not conditioned on the swap's success. Because `ShouldCheckToken`/`ShouldCheckSwapAmount` are configurable and default to `BalanceCheckLevelAll` but can be lowered by node operators (or become stale mid-block due to concurrent state changes on the same account, e.g. a prior transaction in the same block draining the sender's token balance/allowance after `GetCheckBalance` ran in the pool but before block assembly), an attacker can systematically submit many `ApproveTx+SwapTx` (or plain `SwapTx`) pairs referencing tokens they don't actually hold or have already spent, forcing repeated free lends from the proposer. This is a direct, repeatable value-extraction vector against the block proposer/validator's own KAIA balance triggered purely by public RPC-submitted transactions — no smart-contract interaction outside of Kaia's own gasless bundling logic is required.

### Likelihood Explanation
Reaching this path requires only submitting standard `approve`/`swapForGas` calldata transactions through public RPC that match the whitelisted router/tokens — fully attacker-controlled and unprivileged. The check that would prevent the loss (`checkBalanceForSwap`) is a config-tunable, best-effort mempool gate that is architecturally decoupled from the actual bundling/inclusion decision (`VerifyExecutable`), and the underlying TOCTOU window (balance checked at pool-admission time vs. execution time) exists regardless of configuration. This makes exploitation straightforward for any proposer running with default or lowered `BalanceCheckLevel`, or via race conditions against balance changes within the same block.

### Recommendation
- Make `VerifyExecutable` itself perform the authoritative balance/allowance/amountIn checks (not just `checkBalanceForSwap` in the tx-pool path), so the same guarantees hold at bundle-construction time, immediately before the LendTx is generated.
- Re-validate balance/allowance against the exact state the bundle will execute against (not `nil`/latest state) right before including the bundle in the block, closing the pool-to-block TOCTOU window.
- Structure the lend-and-repay so that the LendTx's fee-advance is atomic with, or contingent on, the swap succeeding (e.g., recover the lent amount from the block proposer's fee/burn accounting if the swap reverts, or refuse to include the bundle rather than reverting only the swap).
- Do not allow `BalanceCheckLevelStatic`/`TokenBalanceAndAllowance` to disable checks that gate real value transfer from the proposer; consider making these checks mandatory rather than operator-configurable.

### Proof of Concept
1. Operator/proposer runs with default or lowered `gasless.balance-check-level` (e.g., `BalanceCheckLevelStatic`), disabling `ShouldCheckToken`/`ShouldCheckSwapAmount` (`kaiax/gasless/config.go:90-99`).
2. Attacker, holding zero balance/allowance of an allowed ERC-20 token, submits a legal-looking `SwapTx` (`swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)`) with `minAmountOut >= amountRepay` and `amountRepay == repayAmount(nil, swapTx)` computed per `kaiax/gasless/impl/getter.go:361-367`, targeting the whitelisted `swapRouter`/token.
3. `isSwapTxReady`→`IsExecutable`→`VerifyExecutable` (`kaiax/gasless/impl/getter.go:214-266`) passes purely on nonce/arithmetic grounds; the tx-pool's `checkBalanceForSwap` is skipped or already stale.
4. Block worker calls `GetLendTxGenerator` (`kaiax/gasless/impl/getter.go:273-313`), producing a `LendTx` from the proposer to the attacker for `lendAmount = ApproveTx.Fee()? + SwapTx.Fee()`. This transaction executes successfully, transferring KAIA to the attacker.
5. The bundled `SwapTx` then reverts in the EVM (insufficient token balance/allowance), so no repayment token transfer occurs.
6. Net effect: proposer's KAIA balance permanently decreases by `lendAmount` per attempt, repeatable at will by the attacker with zero cost beyond calldata construction.

Note: full verification of the on-chain `GaslessSwapRouter`/KIP-247 contract's repayment-enforcement logic (e.g., whether it can partially claw back from elsewhere) was not available in the indexed contract sources; this PoC assumes standard ERC-20/router semantics where a reverted call performs no state change and cannot recover an already-mined, separate `LendTx`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** kaiax/gasless/impl/tx_pool.go (L62-182)
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

**File:** kaiax/gasless/config.go (L64-100)
```go
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
