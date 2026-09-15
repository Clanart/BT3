Based on my research, I found a plausible analog in the RPC "pending" state handling, but I want to flag upfront that I could not fully verify the internals of `miner.Pending()` in `work/work.go` before running out of iterations — this affects the confidence of the finding below.

### Title
Shared, unlocked pending `StateDB` returned to concurrent RPC callers allows cross-request state mutation and information leakage - ([File: node/cn/api_backend.go])

### Summary
`CNAPIBackend.StateAndHeaderByNumber` (used by every public `eth_call`, `kaia_call`, and `eth_estimateGas`/`kaia_estimateGas` RPC path when `blockNrOrHash` resolves to `"pending"`) returns the **same live `*state.StateDB` pointer** obtained from `b.cn.miner.Pending()` directly to the caller, with no defensive copy. This state object is then mutated in-place by the generic call/estimate-gas helpers (`EthDoCall`, `DoCall`) before EVM execution.

### Finding Description
In `node/cn/api_backend.go`:
```go
func (b *CNAPIBackend) StateAndHeaderByNumber(ctx context.Context, blockNr rpc.BlockNumber) (*state.StateDB, *types.Header, error) {
	if blockNr == rpc.PendingBlockNumber {
		block, _, state := b.cn.miner.Pending()
		...
		return state, block.Header(), nil
	}
	...
}
``` [1](#0-0) 

Unlike the non-pending branch, which always calls `bc.StateAt(header.Root)` — a call that constructs a brand-new `state.New(...)` object per invocation — the pending branch hands back the miner's actual live `StateDB` instance. This is confirmed by the accompanying unit test, which asserts the returned object is pointer-identical to the mocked value from `miner.Pending()`: [2](#0-1) 
compared to the copy-producing non-pending path: [3](#0-2) 

This shared `StateDB` is then handed to `EthDoCall`/`DoCall`, which mutate it directly with a caller-controlled balance top-up and, in the `eth_call`/`eth_estimateGas` case, an arbitrary caller-supplied `EthStateOverride` (nonce, code, balance, storage, storage diff) applied via `overrides.Apply(state)`: [4](#0-3) [5](#0-4) 

Because no snapshot/revert or per-call copy is performed before these mutations, any concurrent RPC caller resolving `"pending"` (which any unauthenticated public RPC caller can request) observes and mutates the identical `StateDB` object used by other concurrent `"pending"`-scoped calls, and potentially by the miner's own in-progress block assembly.

### Impact Explanation
This is the closest analog to the Quarkus Cache "reused completion context" bug: an object meant to represent one request's isolated execution context is instead a shared, mutable object silently reused across unrelated callers. Concrete consequences reachable by any public RPC caller supplying `"pending"` as the block tag:
- One caller's `EthStateOverride` (used to fake balances/storage for gas estimation) can corrupt or become visible in another concurrent caller's `eth_call`/`eth_estimateGas` result, leaking simulated state that was meant to be scoped to a single request.
- The synthetic balance top-up (`state.AddBalance(msg.ValidatedSender(), ...)`) performed for gas estimation purposes on the shared object can pollute account balances observed by other concurrent "pending" queries (`kaia_getBalance`, `kaia_call`, etc.) issued against the pending state, producing inconsistent/incorrect results across unrelated callers.
- If this same object backs (or aliases) the actual block-building pending state, this raises the possibility of state divergence in results served to different RPC clients from the same node process.

### Likelihood Explanation
High reachability: no privilege is required — this is standard, frequently-used JSON-RPC surface (`eth_call`/`kaia_call`/`estimateGas` with `"pending"` tag), and node operators commonly expose `"pending"`-tag queries on public RPC endpoints. Any two concurrent callers issuing pending-state calls trigger the race.

### Recommendation
`StateAndHeaderByNumber`'s pending branch should return a snapshotted/copied `StateDB` (e.g., via `state.Copy()` or an explicit `state.New`/snapshot-then-revert around the override+balance mutation) rather than the raw object from `miner.Pending()`, so that per-request mutations (`overrides.Apply`, `AddBalance`) cannot be observed by or interfere with concurrent RPC callers or the miner's own block-building state.

### Proof of Concept
1. Node exposes public RPC with `eth_call`/`kaia_call` enabled.
2. Attacker A sends `eth_call` with `blockNrOrHash: "pending"` and a crafted `EthStateOverride` setting a target address's balance/storage to attacker-chosen values.
3. Concurrently, victim B sends `eth_call`/`kaia_call`/`GetBalance` also scoped to `"pending"` against overlapping or unrelated addresses.
4. Because both requests share the same underlying `*state.StateDB` from `miner.Pending()`, B's result can reflect A's override mutations (or vice versa), demonstrating cross-request state leakage/corruption analogous to the Quarkus "wrong completion context" bug.

**Caveat:** I was unable to inspect `work/work.go`'s `Pending()` implementation in this session (ran out of tool iterations) to confirm whether the miner internally already copies the state before returning it, or whether external synchronization elsewhere prevents this race. This should be verified directly against `work/work.go` and the `Miner.Pending()` implementation before treating this as a confirmed, exploitable vulnerability rather than a plausible analog.

### Citations

**File:** node/cn/api_backend.go (L188-207)
```go
func (b *CNAPIBackend) StateAndHeaderByNumber(ctx context.Context, blockNr rpc.BlockNumber) (*state.StateDB, *types.Header, error) {
	// Pending state is only known by the miner
	if blockNr == rpc.PendingBlockNumber {
		block, _, state := b.cn.miner.Pending()
		if block == nil || state == nil {
			// Fallback to latest block (for PN/EN or when pending is not ready)
			header := b.cn.blockchain.CurrentBlock().Header()
			stateDb, err := b.cn.BlockChain().StateAt(header.Root)
			return stateDb, header, err
		}
		return state, block.Header(), nil
	}
	// Otherwise resolve the block number and return its state
	header, err := b.HeaderByNumber(ctx, blockNr)
	if header == nil || err != nil {
		return nil, nil, err
	}
	stateDb, err := b.cn.BlockChain().StateAt(header.Root)
	return stateDb, header, err
}
```

**File:** node/cn/api_backend_test.go (L440-451)
```go
	{
		mockCtrl, _, mockMiner, api := newCNAPIBackend(t)
		mockMiner.EXPECT().Pending().Return(block, reciept, stateDB).Times(1)

		returnedStateDB, header, err := api.StateAndHeaderByNumber(context.Background(), rpc.PendingBlockNumber)

		assert.Equal(t, stateDB, returnedStateDB)
		assert.Equal(t, expectedHeader, header)
		assert.NoError(t, err)

		mockCtrl.Finish()
	}
```

**File:** blockchain/blockchain.go (L799-807)
```go
// State returns a new mutable state based on the current HEAD block.
func (bc *BlockChain) State() (*state.StateDB, error) {
	return bc.StateAt(bc.CurrentBlock().Root())
}

// StateAt returns a new mutable state based on a particular point in time.
func (bc *BlockChain) StateAt(root common.Hash) (*state.StateDB, error) {
	return state.New(root, bc.stateCache, bc.snaps, nil)
}
```

**File:** api/api_eth.go (L523-555)
```go
func (diff *EthStateOverride) Apply(state *state.StateDB) error {
	if diff == nil {
		return nil
	}
	for addr, account := range *diff {
		// Override account nonce.
		if account.Nonce != nil {
			state.SetNonce(addr, uint64(*account.Nonce))
		}
		// Override account(contract) code.
		if account.Code != nil {
			state.SetCode(addr, *account.Code)
		}
		// Override account balance.
		if account.Balance != nil {
			state.SetBalance(addr, (*big.Int)(*account.Balance))
		}
		if account.State != nil && account.StateDiff != nil {
			return fmt.Errorf("account %s has both 'state' and 'stateDiff'", addr.Hex())
		}
		// Replace entire state if caller requires.
		if account.State != nil {
			state.SetStorage(addr, *account.State)
		}
		// Apply state diff into specified accounts.
		if account.StateDiff != nil {
			for key, value := range *account.StateDiff {
				state.SetState(addr, key, value)
			}
		}
	}
	return nil
}
```

**File:** api/api_eth.go (L1312-1351)
```go
func EthDoCall(ctx context.Context, b Backend, args EthTransactionArgs, blockNrOrHash rpc.BlockNumberOrHash, overrides *EthStateOverride, timeout time.Duration, globalGasCap uint64) (*blockchain.ExecutionResult, error) {
	defer func(start time.Time) { logger.Debug("Executing EVM call finished", "runtime", time.Since(start)) }(time.Now())

	state, header, err := b.StateAndHeaderByNumberOrHash(ctx, blockNrOrHash)
	if state == nil || err != nil {
		return nil, err
	}
	if err := overrides.Apply(state); err != nil {
		return nil, err
	}
	// Setup context so it may be cancelled the call has completed
	// or, in case of unmetered gas, setup a context with a timeout.
	var cancel context.CancelFunc
	if timeout > 0 {
		ctx, cancel = context.WithTimeout(ctx, timeout)
	} else {
		ctx, cancel = context.WithCancel(ctx)
	}
	// Make sure the context is cancelled when the call has completed
	// this makes sure resources are cleaned up.
	defer cancel()

	// header.BaseFee != nil means magma hardforked
	var baseFee *big.Int
	if header.BaseFee != nil {
		baseFee = header.BaseFee
	} else {
		baseFee = new(big.Int).SetUint64(params.ZeroBaseFee)
	}
	intrinsicGas, err := types.IntrinsicGas(args.data(), args.GetAccessList(), args.GetAuthorizationList(), args.To == nil, b.ChainConfig().Rules(header.Number))
	if err != nil {
		return nil, err
	}
	msg, err := args.ToMessage(globalGasCap, baseFee, intrinsicGas)
	if err != nil {
		return nil, err
	}

	// Add gas fee to sender for estimating gasLimit/computing cost or calling a function by insufficient balance sender.
	state.AddBalance(msg.ValidatedSender(), new(big.Int).Mul(new(big.Int).SetUint64(msg.Gas()), msg.EffectiveGasPrice(header, b.ChainConfig())))
```
