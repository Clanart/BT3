### Title
Gasless-swap gas lending grants funds to the sender before the price-dependent repayment is re-verified, allowing a TOCTOU spot-price griefing/value-extraction attack against the block proposer - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The Predy Finance incident stemmed from a lending pool that trusted a manipulable spot price/accounting check at one point in time while the actual value-transfer happened later, letting the attacker extract funds without proper repayment. Kaia's `kaiax/gasless` module (KIP-247) has an analogous pattern: the block proposer unconditionally lends KAIA to a gasless-transaction sender based on a swap-price check performed once at tx-pool admission time, but the swap's actual on-chain repayment is only re-verified for arithmetic consistency, not for the live DEX price, at bundling/execution time.

### Finding Description
`checkBalanceForSwap` validates `tx.amountIn >= gsr.getAmountIn(minAmountOut)` by calling the `GaslessSwapRouter` contract's `GetAmountIn` against the *current* chain state at the moment the transaction is admitted to the pool [1](#0-0) . This is a live spot-price read from the router's underlying DEX pool, exactly the class of manipulable value that caused the Predy Finance loss.

Separately, `VerifyExecutable` (invoked via `IsExecutable`, used both by `IsReady` for promotion and by `ExtractTxBundles` for block building) re-checks only deterministic/arithmetic invariants between the approve/swap transactions - sender match, token match, approved amount, nonce sequencing, and that `AmountRepay == repayAmount(approveTxOrNil, swapTx)` - it never re-queries `GetAmountIn` or otherwise re-validates that the swap will still clear at the current market price [2](#0-1) .

Meanwhile, `GetLendTxGenerator` unconditionally constructs a signed transaction from the proposer's node key that transfers `LendAmount = ApproveTx.Fee() + SwapTx.Fee()` (R2+R3) to the sender before the `ApproveTx`/`SwapTx` are executed in the same block bundle [3](#0-2) . `repayAmount` shows the swap is expected to return `R1 (LendTx's own fee) + R2 + R3` back to the proposer via the on-chain `swapForGas` call [4](#0-3) .

Because the price check in `checkBalanceForSwap` (`minAmountOut` vs. live `GetAmountIn`) is a mempool-admission-time snapshot and is not re-verified atomically with block inclusion, a sender can craft a `SwapTx` whose `minAmountOut` barely satisfies the check at submission time, then let (or cause) the pool's exchange rate move unfavorably before the transaction is actually included. When `swapForGas` executes on-chain, the router's own `minAmountOut` slippage guard causes the swap call to revert, so the repayment of `AmountRepay` (which reimburses the proposer's `LendTx` fee, R1) never happens - yet the proposer's `LendTx` has already irrevocably transferred `LendAmount` (R2+R3) to the sender.

### Impact Explanation
Each successfully-admitted-but-later-reverted gasless swap causes the block proposer to pay `R1` (the `LendTx`'s own gas cost) with no corresponding reimbursement, since `VerifyExecutable`/bundling never re-confirms the swap will still execute profitably at inclusion time. This is a repeatable, unauthorized value transfer from the proposer to any unprivileged sender who can submit gasless-swap transactions and influence (or simply wait out) DEX price movement in the `GaslessSwapRouter`'s pool, mirroring the price-check/repayment mismatch pattern that caused fund loss in the Predy Finance incident. At scale (repeated submissions across many blocks) this becomes a systemic proposer-fund-drain / fee-delegation abuse vector rather than a one-off loss.

### Likelihood Explanation
The attack is reachable by any unprivileged transaction sender able to submit a `GaslessApproveTx`/`GaslessSwapTx` pair through the public RPC (`debug_isGaslessTx`/normal tx submission), requiring no special privileges, validator access, or governance control. It only requires timing the transaction so that price moves between mempool-admission and block-inclusion (naturally, or via an attacker-controlled swap on the same pool beforehand), which is a common and low-cost condition for any AMM-priced DeFi flow.

### Recommendation
Re-validate the swap's price condition (`amountIn >= gsr.getAmountIn(minAmountOut)`) against the state that block building/execution will actually use, immediately before including the bundle (e.g., inside `ExtractTxBundles`/`VerifyExecutable`), and/or make the `LendTx` conditional on successful `AmountRepay` transfer (e.g., structure the bundle so `LendTx` value is clawed back or the bundle is dropped) if the swap simulation indicates it will revert. Alternatively, require simulation of the whole bundle at inclusion time and drop bundles whose swap would revert, rather than relying solely on a stale admission-time price check.

### Proof of Concept
1. Attacker holds `token` and drives the `GaslessSwapRouter`'s pool to a rate favorable enough that `checkBalanceForSwap` accepts `AmountIn`/`MinAmountOut` for a `GaslessApproveTx`+`GaslessSwapTx` pair [1](#0-0) .
2. Attacker submits the pair to the mempool; it is admitted since the live-price check passes at that moment.
3. Before the pair is bundled/included, attacker (or natural market activity) shifts the pool price so the required output at execution time falls below `MinAmountOut`.
4. The block proposer's `LendTx` (generated via `GetLendTxGenerator`) unconditionally sends `LendAmount` (R2+R3) to the attacker's address as part of the bundle [3](#0-2) .
5. The `SwapTx` executes `swapForGas` on-chain; the slippage guard (`minAmountOut`) causes it to revert, so `AmountRepay` (including R1, the `LendTx`'s own fee) is never transferred back to the proposer, while the attacker keeps the previously lent `LendAmount`.

Note: The `GaslessSwapRouter` contract's Solidity source (specifically the internal `swapForGas` repayment/revert logic) was not present in the indexed codebase - only the generated Go bindings (`contracts/bindings/kip247/GaslessSwapRouter.go`) and its function signature were available [5](#0-4) . The exact on-revert fund-flow inside `swapForGas` is inferred from the parameter contract (`minAmountOut`, `amountRepay`) and the module's documented fee-lending design rather than directly read from Solidity source; a Devin session with full repo access should confirm this against the actual contract implementation.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L128-142)
```go
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
```

**File:** kaiax/gasless/impl/getter.go (L37-48)
```go
const (
	// import { erc20Abi } from 'viem';
	erc20AbiJson = `[{"type":"event","name":"Approval","inputs":[{"indexed":true,"name":"owner","type":"address"},{"indexed":true,"name":"spender","type":"address"},{"indexed":false,"name":"value","type":"uint256"}]},{"type":"event","name":"Transfer","inputs":[{"indexed":true,"name":"from","type":"address"},{"indexed":true,"name":"to","type":"address"},{"indexed":false,"name":"value","type":"uint256"}]},{"type":"function","name":"allowance","stateMutability":"view","inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"outputs":[{"type":"uint256"}]},{"type":"function","name":"approve","stateMutability":"nonpayable","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"type":"bool"}]},{"type":"function","name":"balanceOf","sta ... (truncated)
	// function swapForGas(address token, uint256 amountIn, uint256 minAmountOut, uint256 amountRepay, uint256 deadline) external
	routerAbiJson = `[{"inputs":[{"internalType":"address","name":"token","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"minAmountOut","type":"uint256"},{"internalType":"uint256","name":"amountRepay","type":"uint256"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapForGas","outputs":[],"stateMutability":"nonpayable","type":"function"}]`
)

var (
	erc20BalanceOfFunc = mustParseAbi(erc20AbiJson, "balanceOf")
	erc20ApproveFunc   = mustParseAbi(erc20AbiJson, "approve")
	routerSwapFunc     = mustParseAbi(routerAbiJson, "swapForGas")
)
```

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
