## Title
Gasless module trusts a Registry-resolved, admin-swappable `GaslessSwapRouter` contract to actually repay lent KAIA, with no on-chain enforcement of repayment - ([File: kaiax/gasless/impl/getter.go])

### Summary
The reported bug class is "external calls to transient/unverified contracts": trusting a dynamically-resolvable, admin-controlled proxy dependency (`FuseAdminProxy`/`Comptroller`) to behave correctly without any code-level verification. Kaia's gasless-transaction feature (KIP-247) has the same structural weakness: the `GaslessSwapRouter` address that the protocol treats as fully trustworthy is not hardcoded or bytecode-checked — it is whatever address is currently registered under the name `GaslessSwapRouterName` in the KIP-149 `Registry` system contract, and this can be changed by the `Registry` owner at any time (with only an activation-block delay). Kaia core lends real KAIA to any sender submitting a valid Approve/Swap pair, and relies entirely on that externally-resolved contract's internal logic to repay the lender — there is no state-level check that repayment actually occurred.

### Finding Description
The `GaslessModule` resolves the trusted swap router dynamically every block via the Registry/MultiCall system contracts: [1](#0-0) 

That address (`g.swapRouter`) is then used as the sole trust anchor for deciding a transaction is a legitimate gasless swap: [2](#0-1) 

Once a transaction is recognized as `IsApproveTx`/`IsSwapTx` targeting `g.swapRouter`, the module unconditionally generates a `LendTx` that transfers real KAIA value from the block proposer's node key to the sender, computed purely from tx fee math, with no dependency on the router contract's actual behavior: [3](#0-2) 

The only "repayment" check performed by Kaia core is a purely calldata-level consistency check (`SP4`) comparing the `amountRepay` field the user *claims* in their swap call to the arithmetic expected value — it never verifies that the router contract will actually transfer that value back to the proposer/coinbase: [4](#0-3) 

`checkBalanceForSwap` performs additional pre-admission sanity checks, but these also call into the same externally-resolved router contract (`routerContract.GetAmountIn`) and are soft/advisory (some are optional via `GaslessConfig.ShouldCheckSwapAmount`), not enforced value-transfer guarantees: [5](#0-4) 

The LendTx and SwapTx are executed atomically as a `Bundle`, so a *reverting* swap does roll back the lend transaction: [6](#0-5) 

However, atomicity only protects the proposer if the router **reverts** on failure. If the registered `GaslessSwapRouter` — a contract whose address and implementation are entirely controlled by whoever administers the KIP-149 `Registry` (`register()` accepts any arbitrary address without any bytecode/behavior validation) — is unverified, buggy, or malicious, and its `swapForGas` function returns success (`ReceiptStatusSuccessful`) without actually transferring `amountRepay` KAIA back to the lender, the bundle is committed as-is: the proposer's LendTx value transfer is permanent, and there is no on-chain mechanism in `kaiax/gasless` or `work/worker.go` that verifies the coinbase/proposer balance actually increased by the expected repay amount post-execution.

This mirrors the external report's exact concern: dependent logic (`PoolManager`/`Comptroller` → `FuseAdminProxy`) blindly trusts an externally-resolved, admin-mutable, and potentially unverified contract to behave correctly, with high trust placed on whoever controls that pointer. In Kaia's case, that pointer is the `Registry` record for `GaslessSwapRouterName`, and the "trusted" caller is the block-production/lending logic that moves real value (KAIA) based on the resolved contract's mere ABI-selector match, not its verified bytecode/behavior.

### Impact Explanation
If the address registered as `GaslessSwapRouter` ever points to an unverified, buggy, or malicious contract implementation (compromised Registry admin key, a rushed/unaudited upgrade, or an implementation bug that silently no-ops the repay transfer while still returning success), any ordinary user can submit a normal-looking gasless Approve+Swap transaction pair. The block proposer's node key would unconditionally lend real KAIA (computed from `lendAmount`) to the sender, and because the router's execution reports success, the atomic-bundle safety net does not trigger a revert — resulting in a direct, repeatable drain of the block proposer's/validator's KAIA with no repayment, reachable by any unprivileged transaction sender.

### Likelihood Explanation
Likelihood depends on the trustworthiness/verifiability of whatever contract is currently registered as `GaslessSwapRouter` in the `Registry`. Since this is fully protocol-external (admin/governance controlled, arbitrary address, no code validation by Kaia core), and the entire value-safety guarantee for gasless lending rests on that external contract behaving correctly, any lapse in verifying/auditing that router (exactly the scenario flagged in the external report about `FuseAdminProxy`) directly and repeatably exposes proposers to fund loss via a completely normal, permissionless gasless-swap transaction flow.

### Recommendation
Do not treat a successful `ReceiptStatusSuccessful` from the swap transaction as sufficient proof of correct repayment. Enforce or verify the actual value/state effect of repayment at the protocol level (e.g., assert the proposer/coinbase balance increased by the exact `amountRepay` after bundle execution, and revert the whole bundle otherwise) rather than delegating this guarantee entirely to the external, Registry-resolved `GaslessSwapRouter` contract's unverified implementation. Additionally, apply stronger governance/validation controls (e.g., require verified bytecode, time-locked multi-party approval) before any address can be registered as `GaslessSwapRouter`/other trust-critical Registry entries that Kaia core treats as safe-to-lend-against.

### Proof of Concept
1. Registry owner (or a compromised/careless admin key) registers a new `GaslessSwapRouter` address in the `Registry` system contract pointing to a contract that implements the `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` selector but internally does nothing (or transfers the tokens elsewhere) and simply returns without reverting — see `getGaslessInfo` for how the module blindly adopts this address (`kaiax/gasless/impl/getter.go:369-389`).
2. After the registration's `activation` block, `g.swapRouter` in the node updates to this new address (`updateAddresses`, `kaiax/gasless/impl/getter.go:315-343`).
3. A normal user submits a valid `ApproveTx` (approving `g.swapRouter` for `MaxUint256`) followed by a `SwapTx` calling `swapForGas` with a self-consistent `amountRepay` matching `repayAmount()` (`kaiax/gasless/impl/getter.go:361-367`) — passing `VerifyExecutable`'s `SP4` check trivially since it is calldata-only.
4. The block proposer's `GetLendTxGenerator` prepends a `LendTx` sending real KAIA to the user (`kaiax/gasless/impl/getter.go:268-313`), bundled atomically with Approve/Swap (`work/worker.go:877-953`).
5. The malicious router's `swapForGas` call succeeds (no revert), so the bundle commits; the proposer's KAIA transfer stands permanently while no repayment reaches the proposer, since nothing in `kaiax/gasless` or `work/worker.go` verifies the coinbase/proposer's balance delta post-execution.

### Citations

**File:** kaiax/gasless/impl/getter.go (L97-103)
```go
func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
}
```

**File:** kaiax/gasless/impl/getter.go (L260-266)
```go
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

**File:** kaiax/gasless/impl/getter.go (L369-389)
```go
func getGaslessInfo(bc backends.BlockChainForCaller, header *types.Header) (common.Address, []common.Address, error) {
	statedb, err := bc.StateAt(header.Root)
	if err != nil {
		return common.Address{}, nil, err
	}

	// If Registry is not installed, do not query GaslessSwapRouter contract.
	if statedb.GetCode(system.RegistryAddr) == nil || bc.Config().IsRandaoForkBlockParent(header.Number) {
		return common.Address{}, nil, nil
	}

	caller, err := system.NewMultiCallContractCaller(statedb, bc, header)
	if err != nil {
		return common.Address{}, nil, err
	}

	opts := &bind.CallOpts{BlockNumber: header.Number}
	info, err := caller.MultiCallGaslessInfo(opts)

	return info.Gsr, info.Tokens, err
}
```

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

**File:** work/worker.go (L877-936)
```go
func (env *Task) commitBundleTransaction(bundle *builder.Bundle, bc BlockChain, nodeAddr common.Address, vmConfig *vm.Config) (error, *types.Transaction, []*types.Log) {
	lastSnapshot := env.state.Copy()
	gasUsedSnapshot := env.header.GasUsed
	blobGasUsedSnapshot := env.header.BlobGasUsed
	blobsSnapshot := env.blobs
	tcountSnapshot := env.tcount
	txs := []*types.Transaction{}
	receipts := []*types.Receipt{}
	logs := []*types.Log{}

	markAllTxUnexecutable := func() {
		for _, txOrGen := range bundle.BundleTxs {
			if txOrGen.IsConcreteTx() {
				tx, _ := txOrGen.GetTx(0)
				tx.MarkUnexecutable(true)
			}
		}
	}

	restoreEnv := func() {
		env.state.Set(lastSnapshot)
		env.header.GasUsed = gasUsedSnapshot
		env.tcount = tcountSnapshot
		// blob related env are restored to the snapshot
		env.header.BlobGasUsed = blobGasUsedSnapshot
		env.blobs = blobsSnapshot
	}

	var totalTxSize uint64 = 0
	for _, txOrGen := range bundle.BundleTxs {
		tx, err := txOrGen.GetTx(env.state.GetNonce(nodeAddr))
		if err != nil {
			logger.Error("TxGenerator error", "error", err)
			markAllTxUnexecutable()
			restoreEnv()
			return kerrors.ErrTxGeneration, nil, nil
		}

		env.state.SetTxContext(tx.Hash(), common.Hash{}, env.tcount)
		receipt, _, err := bc.ApplyTransaction(env.config, &nodeAddr, env.state, env.header, tx, &env.header.GasUsed, vmConfig)
		// Bundled tx will be rejected with any receipt.Status other than success.
		// There may be cases where a revert occurs within the EVM, which could result in an attack on a tx sender in an already executed bundle.
		if err != nil || receipt.Status != types.ReceiptStatusSuccessful {
			if err != vm.ErrInsufficientBalance && err != vm.ErrTotalTimeLimitReached {
				markAllTxUnexecutable()
			}
			receiptStatus := ""
			if receipt != nil {
				receiptStatus = strconv.FormatUint(uint64(receipt.Status), 10)
			}
			logger.Warn("ApplyTransaction error, restoring env",
				"blockNum", env.header.Number.String(), "txHash", tx.Hash().String(),
				"error", err, "receiptStatus", receiptStatus,
			)
			restoreEnv()
			if err == nil {
				err = kerrors.ErrRevertedBundleByVmErr
			}
			return err, tx, nil
		}
```
