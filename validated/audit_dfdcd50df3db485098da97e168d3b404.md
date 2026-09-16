### Title
Gasless swap deadline is validated against the tx-pool's current block time, not the block that will actually execute it, leaving no grace period before on-chain expiry - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`GaslessModule.checkBalanceForSwap` validates a `GaslessSwapTx`'s user-supplied `deadline` against `g.Chain.CurrentBlock().Time()` when deciding whether the tx (and its paired `GaslessApproveTx`) is "ready" to be promoted/bundled. This check has no margin: it only guarantees the deadline has not passed *as of the last mined block*, not that it will still be valid by the time the transaction is actually mined in the *next* block (which is when `GaslessSwapRouter.swap()` on-chain will re-check the deadline).

### Finding Description
`checkBalanceForSwap` is the readiness/eligibility check used by `IsExecutable`/`VerifyExecutable`, which in turn gates whether a `GaslessApproveTx`+`GaslessSwapTx` pair is promoted in the tx pool and bundled with a `LendTxGenerator` at block-building time [1](#0-0) . The deadline check compares the swap's `deadline` field only to the *current* block's timestamp:

```go
// tx.deadline >= currentTimestamp
deadline := swapArgs.Deadline
if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
    return fmt.Errorf("insufficient deadline: ...")
}
``` [1](#0-0) 

This is analogous to the OTLM bug: two time boundaries (the block at which eligibility is *checked* vs. the block at which the action is actually *executed*) are compared with no grace period, so a value that just barely satisfies the check can fail the equivalent check performed later. Here, the block-building pipeline works as follows: `ExtractTxBundles` calls `IsExecutable` (which internally invokes `VerifyExecutable`/`checkBalanceForSwap`) to decide whether to bundle `[LendTxGenerator, ApproveTx, SwapTx]` [2](#0-1) . The `LendTxGenerator` unconditionally transfers the lend amount (covering all three transactions' fees) to the user *before* the swap executes [3](#0-2) [4](#0-3) . The proposer only recoups this lend amount if `GaslessSwapRouter.swap()` executes successfully and repays via `SwappedForGas` [5](#0-4) .

Because the pool-level readiness check uses the *previous* block's timestamp rather than accounting for the timestamp of the block the bundle will actually be included in, a `GaslessSwapTx` whose `deadline` is only marginally in the future (e.g., equal to or a few seconds after `CurrentBlock().Time()`) can be judged "ready" and bundled, yet by the time the block is actually produced and the swap is executed on-chain, the deadline may have already elapsed (block timestamps must be monotonically increasing). If the `GaslessSwapRouter` contract enforces its own on-chain deadline check (standard for router-style swap contracts, matching the `deadline` parameter decoded in `decodeSwapTx`) [6](#0-5) , the swap call reverts, and the previously-sent `LendTx` value transferred to the user is not repaid.

### Impact Explanation
This mirrors the reported bug class of "insufficient grace period between eligibility check and expiry": the proposer's `LendTxGenerator` output already executed and transferred lend value to the user (`SP4`/`repayAmount` computed from `lendAmount`) before the swap is attempted [7](#0-6) , so if the swap subsequently reverts due to the expired deadline, the proposer suffers a concrete, unrecoverable value loss (unrepaid gas lending) — this is a fee-delegation/gasless settlement abuse vector reachable purely by a normal user submitting a `GaslessApproveTx`/`GaslessSwapTx` pair with a `deadline` set close to the current time.

### Likelihood Explanation
Likelihood is moderate: it requires a user (or an adversarial user deliberately targeting proposers) to submit a swap with a `deadline` set to (or just after) the latest known block timestamp. Since block production intervals and mempool propagation delays are variable, this scenario is easily reachable without any privileged access — a single transaction submission suffices.

### Recommendation
Add a safety margin (grace period) to the pool-level deadline check in `checkBalanceForSwap`, e.g., require `deadline >= CurrentBlock().Time() + expectedBlockInterval` (or some configured buffer) instead of a strict `>=` comparison against the last block's timestamp, so that transactions likely to expire before being minable are rejected at promotion time rather than bundled and lent against.

### Proof of Concept
1. User crafts a `GaslessApproveTx` (nonce N) and `GaslessSwapTx` (nonce N+1) with `SwapArgs.Deadline` set equal to `CurrentBlock().Time()` (or `+1`).
2. `checkBalanceForSwap` passes the deadline check at line 177 of `kaiax/gasless/impl/tx_pool.go` because it compares against the already-mined current block's timestamp [1](#0-0) .
3. `IsExecutable`/`ExtractTxBundles` bundles `[LendTxGenerator, ApproveTx, SwapTx]` for the next block [8](#0-7) .
4. The next block is produced with a timestamp strictly greater than the previous block's, now exceeding `SwapArgs.Deadline`.
5. `LendTxGenerator`'s transaction executes first, transferring lend funds to the user [9](#0-8) .
6. `GaslessSwapRouter.swap()` reverts on its own deadline check (standard router behavior implied by the `deadline` parameter), leaving the proposer's lent funds unrepaid.

Note: I could not directly view the `GaslessSwapRouter.sol` source (only the generated Go bindings were indexed) to confirm the exact on-chain deadline revert condition; this assumption is based on the `deadline` field being decoded and validated client-side and is standard for swap-router deadline semantics. If the indexed codebase excludes the actual Solidity source due to size limits, a Devin session with full repository access would be needed to confirm the exact on-chain enforcement path.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L175-179)
```go
	// tx.deadline >= currentTimestamp
	deadline := swapArgs.Deadline
	if deadline.Cmp(g.Chain.CurrentBlock().Time()) < 0 {
		return fmt.Errorf("insufficient deadline: deadline=%s, want=%s", deadline.String(), g.Chain.CurrentBlock().Time().String())
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

**File:** kaiax/gasless/impl/getter.go (L142-180)
```go
func decodeSwapTx(tx *types.Transaction, signer types.Signer) (args *SwapArgs, ok bool) {
	to, inputs, ok := decodeFunctionCall(tx, routerSwapFunc)
	if !ok {
		return nil, false
	}
	token, ok := inputs["token"].(common.Address)
	if !ok {
		return nil, false
	}
	amountIn, ok := inputs["amountIn"].(*big.Int)
	if !ok {
		return nil, false
	}
	minAmountOut, ok := inputs["minAmountOut"].(*big.Int)
	if !ok {
		return nil, false
	}
	amountRepay, ok := inputs["amountRepay"].(*big.Int)
	if !ok {
		return nil, false
	}
	deadline, ok := inputs["deadline"].(*big.Int)
	if !ok {
		return nil, false
	}
	from, err := types.Sender(signer, tx)
	if err != nil {
		return nil, false
	}
	return &SwapArgs{
		Sender:       from,
		Router:       to,
		Token:        token,
		AmountIn:     amountIn,
		MinAmountOut: minAmountOut,
		AmountRepay:  amountRepay,
		Deadline:     deadline,
	}, true
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

**File:** contracts/bindings/kip247/GaslessSwapRouter.go (L1127-1135)
```go
// GaslessSwapRouterSwappedForGas represents a SwappedForGas event raised by the GaslessSwapRouter contract.
type GaslessSwapRouterSwappedForGas struct {
	Proposer        common.Address
	AmountRepaid    *big.Int
	User            common.Address
	FinalUserAmount *big.Int
	Commission      *big.Int
	Raw             types.Log // Blockchain specific contextual infos
}
```
