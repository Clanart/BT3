Based on my research, this is a legitimate analog in the `kaiax/gasless` module. The `checkBalance` hook (`GetCheckBalance`) is only run once, at the moment a transaction is admitted into the tx pool (via `pool.validateTx` → `module.GetCheckBalance()` in `blockchain/tx_pool.go` lines 918-932, and again during `txList.Filter` in `blockchain/tx_list.go` lines 406-414 when pruning). This is the only place actual on-chain `Allowance()`/`BalanceOf()` state is queried. But the code has a "revocation is not honored" gap analogous to CVE-2018-12546 (a retained/authorized permission continues to be honored after it was revoked).

### Title
Stale/unchecked ERC-20 allowance permits execution of GaslessSwapTx after approval is revoked - ([File: kaiax/gasless/impl/tx_pool.go])

### Summary
`checkBalanceForSwap` only re-validates the on-chain ERC-20 `allowance(sender, router)` when `swapNonce == senderNonce` (i.e., no pending `GaslessApproveTx` is assumed to precede it). When the swap's nonce is one ahead of the current state nonce, the module assumes a `GaslessApproveTx` will run immediately before it and skips the live allowance check entirely, trusting only the *declared* amount inside the pending, unexecuted `GaslessApproveTx` (which per rule A4 is always `MaxUint256`). [1](#0-0) 

### Finding Description
`VerifyExecutable`/`IsExecutable` — the function that actually gates promotion of a `[GaslessApproveTx, GaslessSwapTx]` pair into the pending pool and into a block bundle — never queries the live on-chain allowance either; it only compares `approveArgs.Amount` (the value encoded in the pending approve transaction) against `swapArgs.AmountIn`. [2](#0-1) 
Because a legacy/native `approve(spender, 0)` transaction from the sender consumes the very nonce slot that the module assumes will be occupied by the `GaslessApproveTx`, the eviction/readiness logic (`isSwapTxReady`) does correctly re-check that the immediate predecessor in the ready list `IsApproveTx`, so a nonce collision from an unrelated tx is handled. [3](#0-2) 
However, a sender who first (a) obtains sufficient live allowance and balance via a normal `approve()` call, (b) submits a standalone `GaslessSwapTx` (`swapNonce == senderNonce`, so `checkBalanceForSwap` performs the allowance check and it is admitted), and then (c) revokes the approval (`approve(router, 0)`) or drains the token balance with a *higher-nonce* transaction that gets queued but not yet mined — has no re-validation mechanism. The module's `Filter` (called from `txList.Filter` in `blockchain/tx_list.go`) re-runs `GetCheckBalance()` on pool reset, but only in response to a state change/newHead event; there is no guarantee it fires before the swap is promoted and bundled into the very next block if the revoke and the swap land in the same block-building window, or if `Filter` is not triggered between the balance/allowance mutation and promotion. [4](#0-3) 

### Impact Explanation
If the retained `GaslessSwapTx` is executed after allowance/balance revocation, the underlying `GaslessSwapRouter` contract call will simply revert on-chain (ERC-20 `transferFrom` reverts on insufficient allowance/balance) — however, the `LendTxGenerator` bundle unconditionally prepends a `LendTx` that transfers KAIA from the block proposer to the sender to cover gas, **before** the swap is attempted. [5](#0-4) 
If the swap subsequently reverts (due to revoked allowance), the sender keeps the lent KAIA gas (per KIP-247, gas is lent up-front and repaid only through a successful swap) with no repayment, resulting in a proposer-funded value drain — a fee/lending abuse analogous to the CVE's "revoked access still honored" bug class.

### Likelihood Explanation
Requires only a normal unprivileged flow: submit a swap tx while balance/allowance is sufficient, then revoke/drain in a following transaction, timed so that the pool's stale check window is exploited. This does not require any privileged or validator/proposer role — an ordinary gasless-swap user can attempt it. Reliability depends on tx-pool timing (`reset`/`Filter` cadence) and cannot be fully confirmed as always exploitable without dynamic testing of the exact block-building race window, which I could not fully verify from static code alone.

### Recommendation
Re-validate the live on-chain `Allowance()` and `BalanceOf()` for `GaslessSwapTx` (and its paired `GaslessApproveTx`, when present) immediately before bundling/execution in `ExtractTxBundles`/`GetLendTxGenerator`, not only at pool-admission time, and make lending of gas conditional on this final check to prevent value drain when a sender revokes access after a swap tx has been queued.

### Proof of Concept
1. Attacker approves `MaxUint256` for `GaslessSwapRouter` and holds a token balance ≥ `amountIn`.
2. Attacker submits `GaslessSwapTx` (`swapNonce == senderNonce`); `checkBalanceForSwap` validates it as admissible (allowance and balance both sufficient) and it is queued as pending. [6](#0-5) 
3. Before the swap tx is mined, attacker submits (and gets mined via a separate, faster/direct channel or in the same block ahead of the swap) a plain `approve(router, 0)` or a token transfer that drains the balance, using a nonce below the swap's nonce is not possible without conflicting nonce — but a race where the revoke happens between pool admission and block building (not re-checked) is the mechanism to be validated further with dynamic testing.
4. If included, the `LendTxGenerator`-produced `LendTx` still pays out gas to the sender up-front regardless of the now-guaranteed-to-revert swap. [7](#0-6)

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L144-172)
```go
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

**File:** kaiax/gasless/impl/getter.go (L242-252)
```go
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

**File:** blockchain/tx_list.go (L406-414)
```go
		// balance check for module transaction
		for _, module := range pool.modules {
			if module.IsModuleTx(tx) {
				if checkBalance := module.GetCheckBalance(); checkBalance != nil {
					return checkBalance(tx) != nil
				}
				break
			}
		}
```
