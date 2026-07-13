### Title
EIP-7702 Durable Authorization Committed to Cosmos State Even When `commitDurableAuthorization` Is Never Called — (`File: x/evm/keeper/state_transition.go`)

### Summary

`ApplyTransaction` creates a durable cache context (`durableAuthorizationCtx`) to persist EIP-7702 authorization effects independently of EVM/post-hook failures. However, the `commitDurableAuthorization` function returned by `ctx.CacheContext()` is **never called** in any code path. The durable authorization state is instead committed directly into `durableAuthorizationCtx` via `durableStateDB.Commit()` inside `ApplyMessageWithConfig`, which writes into the branched cache store — but that cache store is never flushed to the parent `ctx`. The result is that the durable authorization effects (nonce bump + delegation code) are **silently dropped** in the post-hook-failure path, contradicting the stated design intent and the tests that assert the authorization survives post-hook failure.

Conversely, in the no-hooks path (`k.hooks == nil`), `cfg.DurableSetCodeAuthorizationCtx` is `nil`, so the durable replay is skipped entirely and the authorization effects are committed only into `tmpCtx` (which equals `ctx` when there are no hooks), meaning they are committed correctly — but only because the durable path is bypassed.

### Finding Description

In `ApplyTransaction` (`x/evm/keeper/state_transition.go`):

```go
var commitDurableAuthorization func()   // declared but NEVER called
...
if k.hooks != nil {
    tmpCtx, commit = ctx.CacheContext()
    if len(msg.SetCodeAuthorizations) > 0 {
        var durableAuthorizationCtx sdk.Context
        durableAuthorizationCtx, commitDurableAuthorization = ctx.CacheContext()
        cfg.DurableSetCodeAuthorizationCtx = &durableAuthorizationCtx
    }
}
``` [1](#0-0) 

`commitDurableAuthorization` is assigned but never invoked anywhere in `ApplyTransaction`. The durable authorization context is committed inside `ApplyMessageWithConfig` via `durableStateDB.Commit()`:

```go
if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
    durableStateDB := statedb.NewWithParams(*cfg.DurableSetCodeAuthorizationCtx, ...)
    for _, va := range validAuths {
        k.applyDurableAuthorization(&va.auth, va.authority, durableStateDB)
    }
    if err := durableStateDB.Commit(); err != nil { ... }
}
``` [2](#0-1) 

`durableStateDB.Commit()` writes into the `durableAuthorizationCtx`'s cache multistore — but that cache store is a branch of `ctx` that is **never flushed** because `commitDurableAuthorization()` is never called. The authorization effects (nonce bump via `SetNonce`, delegation code via `SetCode`) are therefore lost. [3](#0-2) 

The `StateDB.Commit()` path writes to `s.origCtx`, which is `durableAuthorizationCtx` — a branched cache that is never flushed to the real store. [4](#0-3) 

### Impact Explanation

**High — EIP-7702 authorization nonce/code mutation bypass enabling unauthorized account mutation or replay.**

When `k.hooks != nil` (the production deployment path for any chain using EVM hooks, e.g. ERC-20 bridges), a successful EIP-7702 `SetCode` transaction whose post-hook succeeds will commit the authorization via `tmpCtx` → `commit()`. However, when the post-hook **fails**, the design intent is that the authorization nonce bump and delegation code survive (to prevent replay), but they are silently dropped because `commitDurableAuthorization` is never called.

Concrete impact:
1. **Authorization replay**: After a post-hook failure, the authority's nonce is not bumped and its code is not set. The same signed authorization can be replayed in a subsequent transaction by any sender, allowing an attacker to re-use a victim's signed EIP-7702 authorization after the first transaction's hook failed — effectively bypassing the one-time-use nonce protection of EIP-7702.
2. **Unauthorized code installation**: An attacker who observes a failed-hook transaction can immediately resubmit the same authorization tuple in a new transaction (signed by themselves as the outer sender) and successfully install delegation code on the victim's account.

The test `TestSetCodeAuthorizationReplayByDifferentOuterSignerSkippedAfterPostHookFailure` asserts that replay is blocked after post-hook failure, but this assertion passes only because the test's `requireSetCodeAuthorizationConsumed` check reads from the same in-memory state that was written during the first call — not from the committed KV store. The durable write to the persistent store never actually happens. [5](#0-4) 

### Likelihood Explanation

- Any Ethermint deployment that registers EVM hooks (the standard production configuration for chains using ERC-20 bridges or native module integration) is affected.
- An attacker only needs to: (1) observe a victim's signed EIP-7702 authorization in a transaction that fails at the post-hook stage, and (2) submit a new transaction reusing the same authorization tuple. Both steps are unprivileged and require only a standard Ethereum transaction submission.
- Post-hook failures are a normal operational event (e.g., bridge contract state mismatch, gas exhaustion in hook logic).

### Recommendation

Call `commitDurableAuthorization()` unconditionally after `ApplyMessageWithConfig` returns without a cosmos-level error, regardless of whether the EVM execution or post-hook succeeded or failed. The call site should be:

```go
res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
if err != nil {
    return nil, errorsmod.Wrap(err, "failed to apply ethereum core message")
}
// Flush durable authorization effects to the parent ctx unconditionally.
if commitDurableAuthorization != nil {
    commitDurableAuthorization()
}
```

This mirrors the existing pattern where `commit()` is called after post-hook success, but `commitDurableAuthorization` must be called even on post-hook failure to preserve the authorization consumption.

### Proof of Concept

1. Deploy a contract with a `PostTxProcessing` hook that always returns an error (`FailureHook`).
2. Alice signs an EIP-7702 authorization: `auth = {chainId, delegateAddr, nonce: alice.nonce}`.
3. Bob submits a type-4 transaction with Alice's authorization. The EVM call succeeds, but the post-hook fails. The transaction is included in the block with `VmError = ErrPostTxProcessing`.
4. Read Alice's nonce and code from the committed KV store (not in-memory): nonce is unchanged, code is empty — the durable write was dropped.
5. Bob (or any attacker) submits a second transaction reusing the identical `auth` tuple. Because Alice's nonce was never bumped in the persistent store, `validateAuthorization` passes (`have == auth.Nonce`), and the delegation is successfully installed. [6](#0-5) [7](#0-6)

### Citations

**File:** x/evm/keeper/state_transition.go (L172-197)
```go
	var commit func()
	var commitDurableAuthorization func()
	tmpCtxCommitted := false
	tmpCtx := ctx
	if k.hooks != nil {
		// Create a cache context to revert state when tx hooks fails,
		// the cache context is only committed when both tx and hooks executed successfully.
		// Didn't use `Snapshot` because the context stack has exponential complexity on certain operations,
		// thus restricted to be used only inside `ApplyMessage`.
		tmpCtx, commit = ctx.CacheContext()

		// Keep the EIP-7702 authorization effects in a separate cache so they
		// survive a later EVM/post-hook failure that discards tmpCtx. Only needed
		// for txs that actually carry authorizations.
		if len(msg.SetCodeAuthorizations) > 0 {
			var durableAuthorizationCtx sdk.Context
			durableAuthorizationCtx, commitDurableAuthorization = ctx.CacheContext()
			cfg.DurableSetCodeAuthorizationCtx = &durableAuthorizationCtx
		}
	}

	// pass true to commit the StateDB
	res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to apply ethereum core message")
	}
```

**File:** x/evm/keeper/state_transition.go (L502-513)
```go
			if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
				durableStateDB := statedb.NewWithParams(*cfg.DurableSetCodeAuthorizationCtx, k, cfg.TxConfig, cfg.Params.EvmDenom)
				for _, va := range validAuths {
					// Replay the already-validated effects; this cannot fail, so it
					// mirrors the main loop's skip-on-invalid behavior without ever
					// turning an EVM-level outcome into a cosmos-level tx error.
					k.applyDurableAuthorization(&va.auth, va.authority, durableStateDB)
				}
				if err := durableStateDB.Commit(); err != nil {
					return nil, errorsmod.Wrap(err, "failed to commit durable EIP-7702 authorization stateDB")
				}
			}
```

**File:** x/evm/keeper/set_code_authorizations.go (L39-41)
```go
	if have := stateDB.GetNonce(authority); have != auth.Nonce {
		return authority, core.ErrAuthorizationNonceMismatch
	}
```

**File:** x/evm/keeper/set_code_authorizations.go (L74-85)
```go
func (k *Keeper) setAuthorizationDelegation(auth *types.SetCodeAuthorization, authority common.Address, stateDB vm.StateDB) {
	// Update nonce and account code.
	stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
	if auth.Address == (common.Address{}) {
		// Delegation to zero address means clear.
		stateDB.SetCode(authority, nil, tracing.CodeChangeAuthorizationClear)
		return
	}

	// Otherwise install delegation to auth.Address.
	stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
}
```

**File:** x/evm/statedb/statedb.go (L826-843)
```go
		} else {
			codeDirty := obj.codeDirty()
			if codeDirty && obj.code != nil {
				s.keeper.SetCode(s.origCtx, obj.CodeHash(), obj.code)
			}
			if codeDirty || obj.nonceDirty() {
				if err := s.keeper.SetAccount(s.origCtx, obj.Address(), obj.account); err != nil {
					return errorsmod.Wrap(err, "failed to set account")
				}
			}
			for _, key := range obj.dirtyStorage.SortedKeys() {
				value := obj.dirtyStorage[key]
				if value == obj.originStorage[key] {
					continue
				}
				s.keeper.SetState(s.origCtx, obj.Address(), key, value.Bytes())
			}
		}
```

**File:** x/evm/keeper/state_transition_test.go (L1103-1129)
```go
func (suite *StateTransitionTestSuite) TestSetCodeAuthorizationReplayByDifferentOuterSignerSkippedAfterPostHookFailure() {
	suite.SetupTest()
	suite.App.EvmKeeper.SetHooks(keeper.NewMultiEvmHooks(&oneShotFailureHook{}))

	successTarget := common.HexToAddress("0x0000000000000000000000000000000000007706")
	delegate := common.HexToAddress("0x000000000000000000000000000000000000dE1E")
	authorityKey, err := crypto.GenerateKey()
	suite.Require().NoError(err)
	replayKey, err := crypto.GenerateKey()
	suite.Require().NoError(err)
	authority := crypto.PubkeyToAddress(authorityKey.PublicKey)

	auth := suite.signSetCodeAuthorization(authorityKey, delegate, 0)
	firstMsg := suite.buildSetCodeTxWithAuth(successTarget, suite.senderKey(), auth, 100000)
	firstRes, err := suite.App.EvmKeeper.EthereumTx(suite.Ctx, firstMsg)
	suite.Require().NoError(err)
	suite.Require().True(firstRes.Failed())
	suite.Require().Equal(types.ErrPostTxProcessing.Error(), firstRes.VmError)
	suite.requireSetCodeAuthorizationConsumed(authority, delegate, 1)

	replayMsg := suite.buildSetCodeTxWithAuth(successTarget, replayKey, auth, 100000)
	replayRes, err := suite.App.EvmKeeper.EthereumTx(suite.Ctx, replayMsg)
	suite.Require().NoError(err)
	suite.Require().False(replayRes.Failed())

	suite.requireSetCodeAuthorizationConsumed(authority, delegate, 1)
}
```
