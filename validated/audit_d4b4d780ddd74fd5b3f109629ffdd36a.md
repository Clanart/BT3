### Title
Silent failure of `PostInsertBlock` in ExecutionModules causes gasless module to use stale swap-router/allowed-token whitelist and stale nonce state, allowing incorrect gasless-transaction classification and gas-fee lending abuse - ([File: work/worker.go])

### Summary
`work/worker.go`'s `handleFinalizedBlock` invokes every registered `kaiax.ExecutionModule.PostInsertBlock(block)` after a block is written, but only logs the returned error instead of propagating or retrying it: [1](#0-0) 

For the `GaslessModule`, `PostInsertBlock` calls `g.Chain.StateAt(block.Header().Root)`, `setCurrentState`, and `updateAddresses` in sequence and returns as soon as `StateAt` fails, meaning `updateAddresses` (which refreshes `swapRouter`/`allowedTokens`) is skipped entirely on that block: [2](#0-1) 

### Finding Description
This mirrors the referenced bug class exactly: an asynchronous/best-effort call (here, an `ExecutionModule.PostInsertBlock` hook run after block finalization) can fail, the failure is only logged with `logger.Error`, and the caller (`handleFinalizedBlock`) proceeds as if the call succeeded — continuing to mine, validate and bundle transactions against stale module state, with no retry, no halt, and no upstream error signal.

For the gasless module specifically, `GaslessModule.currentState`, `swapRouter`, and `allowedTokens` are the sole authorities used to classify and validate gasless transactions:
- `IsApproveTx`/`isApproveTx` and `IsSwapTx`/`isSwapTx` gate on `g.allowedTokens[token]` and `g.swapRouter` [3](#0-2) 
- `VerifyExecutable` checks nonces via `g.getCurrentStateNonce`, which reads from the module's cached `currentState`, not fresh chain state [4](#0-3) 
- Gasless transactions skip the normal sender-balance check (`GetCheckBalance` omitted for gasless txs, per module README) and instead rely entirely on this classification plus the block-proposer-funded `LendTxGenerator`/`GetLendTxGenerator` mechanism, which fronts gas fees for txs recognized as gasless [5](#0-4) 

If `g.Chain.StateAt` transiently fails on a proposer (e.g., pruned/missing trie node during a state-sync race, disk hiccup, or heavy load) — a condition that is not attacker-controlled but is plausible in production — `updateAddresses` is never called for that block, and `g.swapRouter`/`g.allowedTokens`/`g.currentState` (nonce cache) remain frozen at the prior block's values while `commitNewWork` and the tx pool continue to accept, promote, and bundle transactions using this stale whitelist and stale nonces: [6](#0-5) [7](#0-6) 

Because the error is never surfaced to the block-production pipeline (`commitNewWork`/`SubmitTransactions`), there is no mechanism forcing a resync of the module state before further blocks are produced — the staleness persists indefinitely until the next successful `PostInsertBlock` call, and different validators/proposers can diverge in which tokens/router they treat as gasless-eligible if their local `StateAt` failures occur on different blocks.

### Impact Explanation
A stale `allowedTokens`/`swapRouter` cache combined with a stale nonce cache (`currentState`) can cause the gasless-transaction classification and `VerifyExecutable` checks (SP2/SP3/SP4) to be evaluated against outdated on-chain truth. Since gasless transactions bypass the sender-balance precheck and rely on the proposer lending gas via `GetLendTxGenerator`, incorrect/stale classification can result in:
- The proposer lending gas (fronting KAIA) for a swap that no longer satisfies the current on-chain approve/allowance/token-whitelist state, since checks are validated against the frozen `currentState` rather than the real current state.
- State divergence between honest proposers: nodes whose `PostInsertBlock` succeeded vs. failed will bundle/reject gasless bundles differently for the same pending transactions, which can produce different valid blocks for the same height depending on which proposer is selected.

This falls under "fee delegation abuse" / "gasless settlement theft" / "state divergence between honest nodes" categories called out as in-scope impacts.

### Likelihood Explanation
Likelihood is constrained by the fact that `g.Chain.StateAt` failures on a proposer for the head block's root are expected to be rare in a healthy, fully-synced node (state should be available immediately after `WriteBlockWithState`). However, this is not attacker-required — it can occur under normal operational conditions (I/O errors, database corruption, node under heavy load, pruning misconfiguration) without any malicious actor, and once triggered there is no self-healing logic; the code silently locks in stale state indefinitely. This is a genuine root-cause gap (no error handling / no retry / no halt), consistent with the referenced report's bug class, though I could not find a way for an unprivileged transaction sender to directly *trigger* the `StateAt` failure — the trigger condition here is closer to an operational fault than an attacker-controlled input, which lowers confidence versus the original allora finding (where the failing external HTTP call was more directly influenced by network conditions attackers could induce).

### Recommendation
- In `kaiax/gasless/impl/execution.go`'s `PostInsertBlock`, and generally in `work/worker.go`'s `handleFinalizedBlock` loop over `self.executionModules`, do not merely log-and-continue on `PostInsertBlock` errors. At minimum, retry `updateAddresses`/`setCurrentState` on the next block, or track a "stale" flag that causes the gasless (and other) module to refuse to promote/bundle further gasless transactions until state is successfully refreshed, rather than silently operating on outdated whitelist/nonce data.
- Consider surfacing `PostInsertBlock` failures as metrics/alerts distinct from generic `logger.Error`, so operators can detect and react to prolonged staleness.

### Proof of Concept
Not directly reproducible as a single unprivileged transaction/PoC — the root cause requires an internal `StateAt` failure on the proposer node (an operational fault), not attacker-supplied transaction data. I was unable to identify a concrete transaction-only trigger for `g.Chain.StateAt` failing on the freshly-written head block's state root; this weakens the "reachable from a single submitted transaction" bar required by the validation rules. Given this uncertainty about attacker-reachability of the triggering condition, I flag this analog with lower confidence rather than asserting it as a fully proven, directly reachable exploit.

### Citations

**File:** work/worker.go (L386-387)
```go
	// Filter txs with txBundlingModules
	builder.FilterTxs(pending, self.txBundlingModules)
```

**File:** work/worker.go (L553-558)
```go
	// Invoke ExecutionModules after executing a block
	for _, module := range self.executionModules {
		if err := module.PostInsertBlock(block); err != nil {
			logger.Error("Failed to call PostInsertBlock", "err", err)
		}
	}
```

**File:** kaiax/gasless/impl/execution.go (L26-33)
```go
func (g *GaslessModule) PostInsertBlock(block *types.Block) error {
	currentState, err := g.Chain.StateAt(block.Header().Root)
	if err != nil {
		return err
	}
	g.setCurrentState(currentState)
	return g.updateAddresses(block.Header())
}
```

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

**File:** kaiax/gasless/impl/getter.go (L247-258)
```go
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
```

**File:** kaiax/gasless/README.md (L25-36)
```markdown
#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).

### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** kaiax/gasless/impl/init.go (L111-137)
```go
func (g *GaslessModule) setCurrentState(state *state.StateDB) {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	g.currentState = state
}

func (g *GaslessModule) getCurrentStateNonce(addr common.Address) uint64 {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	return g.currentState.GetNonce(addr)
}

func (g *GaslessModule) getCurrentStateBalance(addr common.Address) *big.Int {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	return g.currentState.GetBalance(addr)
}

func (g *GaslessModule) getCurrentHasCode(addr common.Address) bool {
	g.currentStateMu.Lock()
	defer g.currentStateMu.Unlock()

	return g.currentState.GetCodeHash(addr) != types.EmptyCodeHash
}
```
