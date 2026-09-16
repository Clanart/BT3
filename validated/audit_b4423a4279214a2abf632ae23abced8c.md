### Title
Missing deadline validation in gasless `VerifyExecutable`/`IsExecutable` allows the pool/block-builder to accept and bundle expired `SwapForGas` transactions - ([File: kaiax/gasless/impl/getter.go])

### Summary
The gasless module decodes a `Deadline` field from every `swapForGas` transaction but never validates it against the current time/block before deciding the transaction is a valid, executable gasless swap. This mirrors the reported bug class of "missing checks" in a settings/validation function that accepts time-bound parameters without enforcing their temporal constraints.

### Finding Description
`decodeSwapTx` extracts `Deadline` into `SwapArgs.Deadline` [1](#0-0) , but the only consumers of `SwapArgs`, `isSwapTx` and `VerifyExecutable`, never reference `Deadline` at all:

- `isSwapTx` only checks the whitelisted router (S1) and whitelisted token (S3) [2](#0-1) .
- `VerifyExecutable`, which is the authoritative function used by `IsExecutable` to determine whether a gasless approve/swap pair (or lone swap) should be promoted in the tx pool and bundled by the block builder, checks sender match, token match, approve amount, nonce sequencing, and repay amount — but performs no comparison of `swapArgs.Deadline` against `block.timestamp` or any other time reference [3](#0-2) .

Because `IsExecutable`/`VerifyExecutable` is what gates promotion in the tx pool (`isApproveTxReady`/`isReady`) [4](#0-3)  and gates bundle construction (which prepends the proposer-funded `LendTxGenerator`) [5](#0-4) , a swap transaction with an already-expired `deadline` is treated as fully executable by the node/proposer layer. The proposer will still generate and prepend a `LendTx` that funds the sender's gas (`lendAmount`/`repayAmount` computed purely from tx fees, independent of `Deadline`) [6](#0-5) , before the bundle `[LendTxGenerator, ApproveTx, SwapTx]` is submitted for execution.

### Impact Explanation
If the on-chain `GaslessSwapRouter.swapForGas` contract does enforce `deadline` (as the parameter's presence and naming implies), the bundle will revert at execution time after the proposer has already fronted gas via `LendTxGenerator`, but bundle conflict/inclusion logic for gasless bundles is decided purely off `IsExecutable`, meaning a proposer/relayer can be induced to build and attempt landing bundles for stale/expired swap requests, wasting gas-fronting attempts and potentially causing state divergence between honest nodes if node-side and on-chain validation logic disagree on which bundle is "valid" (one full node treating a tx as an executable gasless bundle candidate for building, another rejecting the resultant transaction differently) is possible in edge conditions around expiry timing during block assembly.

### Likelihood Explanation
Any unprivileged user submitting a normal `swapForGas` transaction can set an arbitrary/expired `deadline`; the missing check is triggered unconditionally by ordinary transaction submission, no special privileges required, making this trivially and repeatedly reachable via public RPC.

### Recommendation
Add an explicit deadline check in `VerifyExecutable` (and/or `isSwapTx`) comparing `swapArgs.Deadline` against the current block time/number before treating the swap as executable, returning a new `ErrSwapDeadlineExpired`-style error, consistent with how other temporal fields (nonce sequencing, `mintStart`/`mintEnd` in the analog report) should be range/expiry-validated before being accepted into a promotable/bundleable state.

### Proof of Concept
1. Whitelist a token/router via the gasless config as in existing tests (`testGaslessConfig`) [7](#0-6) .
2. Craft `makeSwapTx` with `Deadline` set to a timestamp already in the past (e.g., `big.NewInt(1)`), similar to `SwapArgs` construction in `helper_test.go`/`getter_test.go` [8](#0-7) .
3. Call `g.IsExecutable(nil, expiredSwapTx)` — observe it returns `true` because no code path inspects `swapArgs.Deadline`, confirmed by the full body of `VerifyExecutable` [9](#0-8) .
4. The tx pool would promote it and the block builder would generate a proposer-funded `LendTxGenerator` bundle for it via `GetLendTxGenerator` [5](#0-4) , despite the transaction being expired by its own declared deadline.

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

**File:** kaiax/gasless/impl/getter.go (L273-313)
```go
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

**File:** kaiax/gasless/impl/tx_pool.go (L251-267)
```go
// isApproveTxReady assumes that the caller checked `g.IsApproveTx(approveTx)`
func (g *GaslessModule) isApproveTxReady(approveTx, nextTx *types.Transaction) bool {
	addr, err := types.Sender(g.signer, approveTx)
	if err != nil {
		return false
	}
	nonce := g.getCurrentStateNonce(addr)

	if approveTx.Nonce() != nonce {
		return false
	}
	if nextTx == nil || !g.IsSwapTx(nextTx) {
		return false
	}

	return g.IsExecutable(approveTx, nextTx)
}
```

**File:** kaiax/gasless/impl/tx_pool_test.go (L38-55)
```go
)

func TestIsModuleTx(t *testing.T) {
	log.EnableLogForTest(log.LvlError, log.LvlTrace)

	g := NewGaslessModule()
	dbm := database.NewMemoryDBManager()
	alloc := testAllocStorage()
	backend := backends.NewSimulatedBackendWithDatabase(dbm, alloc, testChainConfig)
	nodekey, _ := crypto.GenerateKey()
	err := g.Init(&InitOpts{
		ChainConfig:   testChainConfig,
		GaslessConfig: testGaslessConfig,
		NodeKey:       nodekey,
		Chain:         backend.BlockChain(),
		NodeType:      common.ENDPOINTNODE,
	})
	require.NoError(t, err)
```

**File:** kaiax/gasless/impl/getter_test.go (L133-167)
```go
func TestIsExecutable(t *testing.T) {
	log.EnableLogForTest(log.LvlError, log.LvlTrace)

	g := NewGaslessModule()
	db := database.NewMemoryDBManager()
	alloc := testAllocStorage()
	backend := backends.NewSimulatedBackendWithDatabase(db, alloc, testChainConfig)
	key, _ := crypto.GenerateKey()
	err := g.Init(&InitOpts{
		ChainConfig:   testChainConfig,
		GaslessConfig: testGaslessConfig,
		NodeKey:       key,
		Chain:         backend.BlockChain(),
		NodeType:      common.ENDPOINTNODE,
	})
	require.NoError(t, err)

	privkey, _ := crypto.GenerateKey()
	testcases := map[string]struct {
		approve *types.Transaction
		swap    *types.Transaction
		ok      bool
	}{
		"correct gasless tx pair": {
			makeApproveTx(t, privkey, 0, ApproveArgs{Spender: common.HexToAddress("0x1234"), Amount: abi.MaxUint256}),
			makeSwapTx(t, privkey, 1, SwapArgs{Token: common.HexToAddress("0xabcd"), AmountIn: big.NewInt(10), MinAmountOut: big.NewInt(100), AmountRepay: big.NewInt(2021000), Deadline: big.NewInt(300)}),
			true,
		},
		"correct single swap tx": {
			nil,
			makeSwapTx(t, privkey, 0, SwapArgs{Token: common.HexToAddress("0xabcd"), AmountIn: big.NewInt(10), MinAmountOut: big.NewInt(1), AmountRepay: big.NewInt(1021000), Deadline: big.NewInt(300)}),
			true,
		},
		"gasless tx pair with different sender address": {
			makeApproveTx(t, privkey, 0, ApproveArgs{Spender: common.HexToAddress("0x1234"), Amount: abi.MaxUint256}),
```
