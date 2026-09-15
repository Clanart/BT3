### Title
Gasless user can drain the swap-token balance to obtain proposer-lent gas without repayment - ([File: kaiax/gasless/impl/getter.go])

### Summary
The Footium report describes a class of bug where a party in control of escrowed/backing assets can unilaterally withdraw them before a counter-party's transaction (which relies on those assets remaining available) completes, leaving the counter-party with an empty commitment. In `kaiax/gasless`, the block proposer unconditionally lends KAIA to a gasless-transaction sender via `GetLendTxGenerator`, expecting repayment through the bundled `GaslessSwapTx`. The sender fully controls the token balance backing that repayment and the module's readiness/execution checks never verify the sender's actual token balance, only the `approve` amount, so the sender can drain the token balance right before the swap executes, causing the swap to revert while the lend has already paid out — an analog to the seller draining the FootiumEscrow before the buyer's transaction completes.

### Finding Description
The gasless flow bundles `[LendTxGenerator, GaslessApproveTx?, GaslessSwapTx]` and the `LendTxGenerator` produces an unconditional value transfer from the proposer to the gasless sender for the full lend amount before the swap is attempted: [1](#0-0) 

Repayment is only enforced inside the swap contract call itself (`swapForGas`), which is a separate, later transaction in the bundle — the module verifies only the *declared* `amountRepay` matches the computed formula, never the sender's actual on-chain token balance at the time of building or execution: [2](#0-1) 

The design explicitly documents that balance checks are skipped for gasless transactions: [3](#0-2) 

Because the sender fully controls their own token balance, they can submit `GaslessApproveTx` (satisfying `SP1`/`SP2` allowance checks) and then, in the same nonce sequence or via another transaction that executes before the swap settles, transfer/burn/move the tokens elsewhere so that when `GaslessSwapTx` finally executes on-chain, `swapForGas` reverts for insufficient balance. Since the `LendTx` in the bundle already unconditionally sent KAIA to the sender beforehand, the proposer's lent KAIA is not recovered.

### Impact Explanation
This results in unauthorized value movement / fee-delegation (lending) abuse: the block proposer loses real KAIA value lent to a malicious gasless-transaction sender who never repays. Because the lend transaction execution is not conditioned on (nor atomically tied to) the swap's success or a live balance check, this is a concrete "buyer loses escrowed value" pattern — the proposer is the buyer who advances value on the expectation of repayment, and the gasless sender is the "seller" who can empty the position (their token balance) before the settlement transaction runs, exactly mirroring the escrow-drain bug class in the report.

### Likelihood Explanation
Any account holding the whitelisted gasless token can perform this by simply submitting an `approve` for the swap router, then moving/spending its token balance elsewhere before its own `swapForGas` call is processed (or crafting a competing transfer to land in the same block ahead of the swap). No special privileges, node access, or governance rights are needed — only a public RPC/tx-pool submission from an unprivileged sender, matching the "single submitted transaction/bundle" reachability requirement.

### Recommendation
- Verify the sender's actual token balance (and allowance) against `amountIn`/`amountRepay` at the moment the bundle is built and again immediately before execution, not just the static `approve` amount.
- Make the lend transaction's payout conditional on (or atomically reversible with) the swap's success, e.g., by validating balance in the `GaslessSwapRouter` contract itself and reverting the whole bundle (including refusing the lend) if the swap cannot succeed.
- Consider re-validating gasless bundle executability against the latest state right before inclusion in the block to close the window between approval and swap execution.

### Proof of Concept
1. Attacker (gasless token holder) submits `GaslessApproveTx` approving `MaxUint256` to the whitelisted `GaslessSwapRouter` — satisfies `IsApproveTx`/`A1-A4` checks in `kaiax/gasless/impl/getter.go`.
2. Bundle builder generates `[LendTxGenerator, ApproveTx, SwapTx]` per `kaiax/gasless/impl/builder.go` `ExtractTxBundles`, prepaying the attacker's gas fee via `GetLendTxGenerator` (unconditional KAIA transfer).
3. Before/within the same execution window, attacker moves out or spends the token balance backing `AmountIn` (e.g., via a prior transfer in the same nonce sequence or another account interaction), so that by the time `GaslessSwapTx` calls `swapForGas`, the token balance is insufficient and the swap call reverts.
4. The `LendTx` has already unconditionally transferred KAIA to the attacker; because there is no balance check (`kaiax/gasless/README.md` line 27) nor atomic linkage between lend success and swap success, the proposer's lent KAIA is not repaid — a direct value loss to the proposer analogous to the buyer receiving an emptied escrow.

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

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```
