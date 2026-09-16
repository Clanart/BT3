### Title
Registry owner can rug gasless-swap lenders and brick in-flight GaslessTx bundles by swapping the trusted `GaslessSwapRouter` address - ([File: kaiax/gasless/impl/getter.go])

### Summary
The `kaiax/gasless` module (KIP-247) fully trusts whatever address the KIP-149 `Registry` currently reports for `GaslessSwapRouter` when deciding what counts as a valid approve/swap pair, when computing the CN's lend amount, and when validating the swap economics. This mirrors the audited "Governance can brick Safes" pattern: a single privileged on-chain actor (here, the `Registry` owner instead of `governance`) can, with one `register()` call, redirect the address that unprivileged users' already-signed transactions implicitly depend on, either bricking pending gasless bundles or letting a malicious router manipulate the CN lender's repayment accounting.

### Finding Description
`GaslessModule.updateAddresses` re-reads the active `GaslessSwapRouter` address from the `Registry`/`MultiCall` contracts on every block and overwrites `g.swapRouter` unconditionally: [1](#0-0) 

This address is then used as the sole source of truth for:
- Whether a pending approve/swap transaction is recognized as a `GaslessTx` at all (`isApproveTx`/`isSwapTx` compare `args.Spender`/`args.Router` against `g.swapRouter`): [2](#0-1) 
- The swap economics check, which calls `GetAmountIn` directly on whatever contract currently sits at `swapRouter`, with no bytecode/interface pinning: [3](#0-2) 
- The CN's lend transaction, which sends real KAIA to the sender based on `lendAmount`/`repayAmount` computed purely from the approve/swap tx fees, expecting the swap to later "repay" the lender via the router: [4](#0-3) 

The `Registry` contract (KIP-149) allows its `owner` to call `register(name, addr, activation)` with an arbitrary activation block (including immediately/the next block), instantly swapping the `GaslessSwapRouter` record used by every full node: [5](#0-4) [6](#0-5) 

This is structurally identical to the audited bug: `SafeModeratorOverridable`'s override safety net depended on an address (`TransactionValidator`) that a single privileged party (`governance`/`AddressProvider`) could swap out, defeating a mechanism ordinary users relied on. Here, ordinary gasless users' approve/swap transactions (and the CN's own lending flow) depend on the `GaslessSwapRouter` address resolved from the `Registry`, which the `Registry` owner can swap at will:
1. **Bricking (DoS analog):** If the owner registers a new router mid-flight, previously valid pending `GaslessApproveTx`/`GaslessSwapTx` pairs immediately fail `isApproveTx`/`isSwapTx` (spender/router mismatch), stranding users' approvals and nonces exactly like the "brick Safes" scenario — the override/verification path a user relies on is silently redirected by a privileged party.
2. **Value-extraction risk:** Because `checkBalanceForSwap` calls `GetAmountIn` on the *current* `swapRouter` with no code/interface validation, and the CN pre-lends real KAIA (`lendAmount`) trusting the swap tx's declared `amountRepay`, a malicious router registered by the `Registry` owner can return manipulated `GetAmountIn`/swap results, causing the CN lender to lend KAIA that is not properly repaid, or causing user token approvals to be exploited under the new router's logic.

### Impact Explanation
- CN nodes acting as gasless lenders can have their `lendAmount` KAIA advanced based on economics validated against an attacker-controlled router contract, risking loss of lent funds (fee/value-abuse class).
- Ordinary gasless users lose intended transactions (approve/swap) with no recourse, since the router address they targeted is unilaterally replaced — a bricking/DoS impact directly analogous to the referenced report.
- Because the address swap takes effect at the next processed block (activation is attacker-chosen), it can be timed to land exactly when specific pending gasless bundles are in the pool, maximizing damage window.

### Likelihood Explanation
Requires control of the `Registry` contract's `owner` key (a privileged/governance-tier actor), exactly the same privilege level as the original report's malicious `governance`. Given that `register()` has no timelock enforcement in the mock/reference implementation (activation can be immediate) and the gasless module blindly re-reads and trusts the resolved address with no additional invariant checks (e.g., pinned interface/version, minimum notice period), a compromised or malicious owner key directly and immediately achieves the impact.

### Recommendation
- Require a minimum timelock/notice period between `Registry.register()` calls for `GaslessSwapRouter` and their activation, so in-flight `GaslessTx` bundles have a chance to complete or be safely rejected before the swap.
- Have `GaslessModule` validate the router's expected interface/bytecode hash (or require an explicit migration/drain step) before trusting a newly-registered address for lending decisions.
- Consider requiring the CN to independently re-verify `GetAmountIn` results against an oracle/expected AMM invariant rather than trusting the router unconditionally, or capping/insuring the lender's exposure per swap.

### Proof of Concept
1. Registry owner calls `Registry.register("GaslessSwapRouter", maliciousRouter, currentBlock+1)` (as shown feasible in `TestPostInsertBlock`'s "update gsr address" case, which uses immediate registration and is picked up within one block).
2. On the next block, every full node's `GaslessModule.updateAddresses` sets `g.swapRouter = maliciousRouter`.
3. Any user's already-broadcast `GaslessApproveTx`/`GaslessSwapTx` pair targeting the old, legitimate router now fails `isApproveTx`/`isSwapTx` checks (bricked), or — if the malicious router mimics the KIP-247 interface — `checkBalanceForSwap`'s `GetAmountIn` call returns attacker-chosen values that the CN's `GetLendTxGenerator` lending flow trusts, letting the attacker manipulate `lendAmount`/`repayAmount` economics to the CN's detriment. [6](#0-5)

### Citations

**File:** kaiax/gasless/impl/getter.go (L74-103)
```go
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

**File:** kaiax/gasless/impl/tx_pool.go (L107-142)
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
```

**File:** contracts/testing/system_contracts/RegistryMock.sol (L27-32)
```text
    function register(string memory name, address addr, uint256 activation) public override {
        if (records[name].length == 0) {
            names.push(name);
        }
        records[name].push(Record(addr, activation));
    }
```

**File:** kaiax/gasless/impl/execution_test.go (L118-142)
```go
		"update gsr address": {
			func() *GaslessModule {
				g := NewGaslessModule()
				dbm := database.NewMemoryDBManager()
				backend := backends.NewSimulatedBackendWithDatabase(dbm, alloc, testChainConfig)
				err := g.Init(&InitOpts{
					ChainConfig:   testChainConfig,
					GaslessConfig: testGaslessConfig,
					NodeKey:       nodekey,
					Chain:         backend.BlockChain(),
					NodeType:      common.ENDPOINTNODE,
				})
				require.NoError(t, err)

				sender := bind.NewKeyedTransactor(senderkey)
				contract, _ := contracts.NewRegistryMockTransactor(system.RegistryAddr, backend)
				contract.Register(sender, GaslessSwapRouterName, anotherGSR, big.NewInt(2))

				backend.Commit()
				backend.Commit()
				return g
			},
			anotherGSR,
			[]common.Address{dummyTokenAddress1},
		},
```
