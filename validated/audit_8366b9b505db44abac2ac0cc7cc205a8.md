## Title
Governance-controlled `GaslessSwapRouter` address in the KIP-149 Registry is trusted without validation, allowing a proposer's lent gas fee to be stolen if a malicious/buggy router is registered - (File: kaiax/gasless/impl/getter.go)

### Summary
This is a direct analog of the reported "yieldTrackers can be changed anytime" bug class: an externally-controlled, governance-mutable address is consumed by a core module without any sanity checks on the contract it points to, and the module's security-critical logic (balance/collateral checks) fully defers to that external, changeable contract's own return values.

### Finding Description
The `kaiax/gasless` module determines which router (`swapRouter`) and tokens are eligible for gasless transactions by reading the `GaslessSwapRouter` (GSR) address from the KIP-149 `Registry` system contract on every block via `updateAddresses`/`getGaslessInfo`, and simply overwrites its in-memory `swapRouter` and `allowedTokens` with whatever the registry currently reports, with no validation of the contract's bytecode or behavior: [1](#0-0) [2](#0-1) 

The `Registry` address for `GaslessSwapRouterName` is set via `Registry.register(name, addr, activation)`, which is a governance/owner-controlled call analogous to the report's yieldTracker setter, changeable at any time by the registry owner: [3](#0-2) [4](#0-3) 

Once a given address is registered as GSR, the gasless module blindly trusts it for two purposes:
1. Classifying transactions as gasless approve/swap (`isApproveTx`/`isSwapTx`), based solely on `tx.to == swapRouter`: [5](#0-4) 

2. Validating that a swap is safely collateralized, by directly calling into the **same untrusted router contract's own `GetAmountIn` view function** to check sufficiency of `AmountIn`: [6](#0-5) 

Critically, per the module's own documentation, the sender's balance check is explicitly **omitted** for gasless transactions, because the proposer (consensus node) is expected to be repaid via the swap itself: [7](#0-6) 

The proposer/CN then generates and signs a `LendTx` from its own key that pays the swap's declared fee upfront, expecting repayment through the swap execution against the registered router: [8](#0-7) 

Because `getAmountIn` used for collateral verification is a call into the very same contract that governance can freely swap out, a router that is malicious or simply buggy can report an artificially low required `amountIn` (or otherwise misrepresent solvency), passing `checkBalanceForSwap` while the actual on-chain `swapForGas` execution fails to fully collect `amountRepay`. Since the CN already lent the gas fee via `LendTx` before verifying the swap actually executes correctly on-chain, this results in unrecoverable loss of the block proposer's funds.

### Impact Explanation
Exactly as flagged in the original report for yieldTrackers, an address that "governance" can repoint at any time is consumed downstream without independent validation. Here the consequence is concrete: unauthorized value movement out of the block proposer's (consensus node's) wallet, since the fee-lending mechanism relies entirely on the mutable, ungoverned-at-the-code-level router contract to self-report solvency and correctly repay. If the registered GSR contract is later swapped to a version with different/buggy `getAmountIn`/`swapForGas` semantics, an attacker (any unprivileged sender submitting a crafted approve+swap bundle) can trigger fee-lending payouts that are never repaid.

### Likelihood Explanation
Registering/updating the `GaslessSwapRouter` in the `Registry` is a governance/owner-privileged action (matching the original report's "gov" actor), so this is not exploitable by a fully unprivileged party without that registry change first occurring. However, once such a change happens (upgrade, migration, misconfiguration, or a compromised/careless registry owner), **any unprivileged transaction sender** can immediately exploit the gap by submitting ordinary gasless approve/swap transactions, since the module performs no additional verification beyond querying the (now-untrusted) router itself.

### Recommendation
- Do not rely solely on the registered router's own view functions (`GetAmountIn`) for solvency verification; add protocol-level invariants that are independent of the router's code (e.g., recompute expected repayment via a fixed/whitelisted price oracle, or require the router to be re-validated/re-audited against a known interface hash before being trusted for lending).
- Verify actual repayment post-execution and roll back/refuse to include the bundle if the LendTx is not fully repaid within the same atomic bundle, rather than relying on pre-execution checks against the router.
- Add a governance-side timelock/allowlist and interface/bytecode validation for any address registered under `GaslessSwapRouterName`, and consider decentralizing/hardening the Registry owner-change process (multisig/governance vote) as recommended in the original report.

### Proof of Concept
1. Registry owner (governance) calls `Registry.register("GaslessSwapRouterName", maliciousRouter, activation)` as shown in [4](#0-3) , pointing GSR to a contract whose `getAmountIn()` always returns `0` (or an arbitrarily low value) and whose `swapForGas` does not actually collect `amountRepay`.
2. After the activation block, `updateAddresses` picks up `maliciousRouter` as `g.swapRouter` [9](#0-8) .
3. An unprivileged user submits a `GaslessApproveTx` + `GaslessSwapTx` pair targeting `maliciousRouter` with `amountIn` far below actual needed value; `checkBalanceForSwap` passes because it queries `maliciousRouter.GetAmountIn` directly [6](#0-5) .
4. The proposer includes the bundle, prepending a signed `LendTx` from its own funds [8](#0-7) ; the malicious router's `swapForGas` executes without repaying `amountRepay`, resulting in a net loss of KAIA from the block proposer's account.

### Citations

**File:** kaiax/gasless/impl/getter.go (L79-103)
```go
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

**File:** kaiax/gasless/impl/getter.go (L315-344)
```go
func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}

	g.swapRouter = swapRouter

	g.allowedTokens = map[common.Address]bool{}
	for _, addr := range tokens {
		// all tokens are allowed if nil
		if g.GaslessConfig.AllowedTokens == nil {
			g.allowedTokens[addr] = true
		}
		for _, allowed := range g.GaslessConfig.AllowedTokens {
			if addr == allowed {
				g.allowedTokens[addr] = true
			}
		}
	}

	return nil
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

**File:** contracts/bindings/kip149/Registry.go (L396-401)
```go
// Register is a paid mutator transaction binding the contract method 0xd393c871.
//
// Solidity: function register(string name, address addr, uint256 activation) returns()
func (_IRegistry *IRegistryTransactor) Register(opts *bind.TransactOpts, name string, addr common.Address, activation *big.Int) (*types.Transaction, error) {
	return _IRegistry.contract.Transact(opts, "register", name, addr, activation)
}
```

**File:** tests/gasless_test.go (L107-121)
```go
	/* ------------- Register GaslessSwapRouter address in Registry ------------- */
	// send register tx
	targetBlockNum := new(big.Int).Add(node.BlockChain().CurrentHeader().Number, big.NewInt(4))
	registry, err := kip149contract.NewRegistry(system.RegistryAddr, transactor)
	if err != nil {
		t.Fatal(err)
	}
	registerTx, err := registry.Register(bind.NewKeyedTransactor(owner.Keys[0]), gaslessImpl.GaslessSwapRouterName, gsrAddr, targetBlockNum)
	if err != nil {
		t.Fatal(err)
	}
	registerTxReceipt := waitReceipt(chain, registerTx.Hash())
	if registerTxReceipt == nil || registerTxReceipt.Status != types.ReceiptStatusSuccessful {
		t.Fatal("failed to registor GaslessSwapRouter address")
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

**File:** kaiax/gasless/README.md (L25-27)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```
