### Title
EIP-7702 Authorization Consumed and Committed Even When EVM Execution Fails Due to Post-Hook Revert - (`x/evm/keeper/state_transition.go`)

### Summary
The `ApplyTransaction` function in Ethermint's EVM keeper unconditionally commits EIP-7702 authorization effects (authority nonce bump + delegation code) to durable state even when the EVM call itself reverts or the post-processing hook fails. This is an intentional design choice, but it creates a direct analog to the H-02 "bypass via wrapping" vulnerability class: an unprivileged attacker can craft an EIP-7702 transaction targeting a contract that is guaranteed to revert, forcing the victim authority's account to be permanently mutated (nonce incremented, delegation code installed) without any successful EVM execution, and without the attacker paying for the effects beyond gas.

### Finding Description

In `ApplyTransaction` (`x/evm/keeper/state_transition.go`), when hooks are registered, a separate `durableAuthorizationCtx` cache context is created as a sibling of the main `tmpCtx`:

```go
if len(msg.SetCodeAuthorizations) > 0 {
    var durableAuthorizationCtx sdk.Context
    durableAuthorizationCtx, commitDurableAuthorization = ctx.CacheContext()
    cfg.DurableSetCodeAuthorizationCtx = &durableAuthorizationCtx
}
```

Inside `ApplyMessageWithConfig`, before `evm.Call()` is invoked, all valid authorizations are applied to the main `stateDB` **and** immediately replayed into `durableStateDB` and committed:

```go
if commit && cfg.DurableSetCodeAuthorizationCtx != nil && len(validAuths) > 0 {
    durableStateDB := statedb.NewWithParams(*cfg.DurableSetCodeAuthorizationCtx, ...)
    for _, va := range validAuths {
        k.applyDurableAuthorization(&va.auth, va.authority, durableStateDB)
    }
    if err := durableStateDB.Commit(); err != nil { ... }
}
```

Back in `ApplyTransaction`, after `ApplyMessageWithConfig` returns, the durable commit is flushed unconditionally whenever `tmpCtxCommitted` is false — which is the case for **both** EVM-level reverts (failed execution) and post-hook failures:

```go
if commitDurableAuthorization != nil && !tmpCtxCommitted {
    commitDurableAuthorization()
}
```

`tmpCtxCommitted` is only set to `true` when `commit()` is called, which only happens when `PostTxProcessing` succeeds. For EVM reverts (`res.Failed() == true`) and post-hook failures, `tmpCtxCommitted` remains `false`, so `commitDurableAuthorization()` is always called.

The result: the authority's nonce is bumped by 1 and the delegation code (`0xef0100 || delegate_address`) is written to the authority's account in committed state, even though the EVM call reverted and no useful work was done.

The `validateAuthorization` function checks `stateDB.GetNonce(authority) == auth.Nonce`. Once the durable commit fires, the authority's on-chain nonce is `auth.Nonce + 1`. Any subsequent attempt to replay the same signed authorization will fail with `ErrAuthorizationNonceMismatch`, permanently consuming the authorization.

### Impact Explanation

**High — EIP-7702 authorization signer verification bypass enabling unauthorized account/code mutation.**

An attacker (outer tx signer) can:
1. Obtain a signed EIP-7702 authorization from a victim (e.g., via a phishing-free, purely on-chain mechanism such as a public authorization marketplace or a previously broadcast but unconfirmed authorization).
2. Submit a `SetCodeTx` targeting a contract address that is guaranteed to revert (e.g., a contract containing `INVALID` / `0xfe`, or one that always reverts).
3. The EVM call reverts (`res.Failed() == true`), so `tmpCtx` is never committed and no EVM state changes persist.
4. However, `commitDurableAuthorization()` fires unconditionally, writing the authority's nonce bump and delegation code to committed state.
5. The victim's account now has an unwanted delegation code installed and their nonce is permanently incremented, without their consent and without any successful execution.

This constitutes unauthorized account/code mutation of a third-party EOA through a valid unprivileged transaction submission path.

Additionally, the installed delegation code (`0xef0100 || attacker_contract`) means the victim's EOA will now execute the attacker's contract logic on any future call to the victim's address, enabling balance theft if the attacker's delegate drains `SELFBALANCE` on the next call.

### Likelihood Explanation

- Requires only a valid signed EIP-7702 authorization from the victim, which can be obtained if the victim ever broadcast such an authorization (e.g., for a different purpose or chain).
- The attacker controls the outer transaction signer and the `to` address (the reverting contract).
- No privileged access, validator compromise, or governance action is needed.
- The chain must be running Prague fork (EIP-7702 enabled), which is the current configuration per the codebase.
- The test `TestSetCodeAuthorizationSurvivesFailedExecutionWithAndWithoutHooks` explicitly asserts this behavior as correct (`suite.requireSetCodeAuthorizationConsumed(authority, delegate, 1)` after a failed execution), confirming the code path is reachable and the mutation is committed.

### Recommendation

The durable authorization commit should only fire when the EVM execution **succeeds** (i.e., `!res.Failed()`), matching Ethereum mainnet semantics where authorization effects are part of the transaction's state transition and are rolled back on revert. Specifically:

In `ApplyTransaction`, change the condition from:
```go
if commitDurableAuthorization != nil && !tmpCtxCommitted {
    commitDurableAuthorization()
}
```
to:
```go
if commitDurableAuthorization != nil && !tmpCtxCommitted && !res.Failed() {
    commitDurableAuthorization()
}
```

Alternatively, if the design intent is to consume authorizations even on revert (to prevent replay), the nonce bump should still be committed but the delegation code installation should be conditional on execution success, matching go-ethereum's behavior where `applyAuthorization` is called before `evm.Call` but the effects are part of the EVM state that gets reverted on failure.

### Proof of Concept

The existing test `TestSetCodeAuthorizationSurvivesFailedExecutionWithAndWithoutHooks` in `x/evm/keeper/state_transition_test.go` directly demonstrates the issue:

```go
msg := suite.buildSetCodeTx(failingTarget, authorityKey, delegate, 0, 100000)
res, err := suite.App.EvmKeeper.EthereumTx(suite.Ctx, msg)
suite.Require().NoError(err)
suite.Require().True(res.Failed())  // EVM reverted
suite.requireSetCodeAuthorizationConsumed(authority, delegate, 1)  // But auth was committed!
```

An attacker exploiting this:
1. Obtains `auth = SignSetCode(victimKey, {delegate: attackerContract, nonce: victimNonce})`
2. Deploys `attackerContract` with code `0xfe` (INVALID opcode — always reverts)
3. Submits `SetCodeTx{to: attackerContract, authList: [auth]}`
4. EVM reverts, but `commitDurableAuthorization()` fires
5. Victim's account now has `code = 0xef0100 || attackerContract` and `nonce = victimNonce + 1`
6. Attacker redeploys `attackerContract` with drain logic (`CALL(CALLER, SELFBALANCE, ...)`)
7. Any future call to victim's address executes the drain logic [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** x/evm/keeper/state_transition.go (L176-191)
```go
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
```

**File:** x/evm/keeper/state_transition.go (L229-268)
```go
	if !res.Failed() {
		receipt.Status = ethtypes.ReceiptStatusSuccessful
		// Only call hooks if tx executed successfully.
		if err = k.PostTxProcessing(tmpCtx, msg, receipt); err != nil {
			// If hooks return error, revert the whole tx.
			res.VmError = types.ErrPostTxProcessing.Error()
			k.Logger(ctx).Error("tx post processing failed", "error", err)

			// If the tx failed in post processing hooks, we should clear the logs
			res.Logs = nil
		} else if commit != nil {
			// PostTxProcessing is successful, commit the tmpCtx
			commit()
			tmpCtxCommitted = true
			// Since the post-processing can alter the log, we need to update the result
			res.Logs = types.NewLogsFromEth(receipt.Logs)
		}
	}

	// Get the tracer and add OnGasChange hook for gas refund
	leftoverGas := msg.GasLimit - res.GasUsed

	// refund gas in order to match the Ethereum gas consumption instead of the default SDK one.
	if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil {
		return nil, errorsmod.Wrapf(err, "failed to refund leftover gas to sender %s", msg.From)
	}

	tracer := cfg.GetTracer()
	if tracer != nil && tracer.OnGasChange != nil {
		tracer.OnGasChange(leftoverGas, 0, tracing.GasChangeTxLeftOverReturned)
	}

	totalGasUsed, err := k.AddTransientGasUsed(ctx, res.GasUsed)
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to add transient gas used")
	}

	if commitDurableAuthorization != nil && !tmpCtxCommitted {
		commitDurableAuthorization()
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

**File:** ante/eth.go (L93-99)
```go
		if !rules.IsPrague {
			if acct.IsContract() {
				fromAddr := common.BytesToAddress(from)
				return errorsmod.Wrapf(errortypes.ErrInvalidType,
					"the sender is not EOA: address %s, codeHash <%s>", fromAddr, acct.CodeHash)
			}
		}
```

**File:** x/evm/keeper/state_transition_test.go (L871-912)
```go
func (suite *StateTransitionTestSuite) TestSetCodeAuthorizationSurvivesFailedExecutionWithAndWithoutHooks() {
	testCases := []struct {
		name       string
		setupHooks func()
	}{
		{
			name: "no hooks",
		},
		{
			name: "hooks enabled",
			setupHooks: func() {
				suite.App.EvmKeeper.SetHooks(keeper.NewMultiEvmHooks(&LogRecordHook{}))
			},
		},
	}

	for _, tc := range testCases {
		suite.Run(tc.name, func() {
			suite.SetupTest()
			if tc.setupHooks != nil {
				tc.setupHooks()
			}

			failingTarget := common.HexToAddress("0x0000000000000000000000000000000000007702")
			delegate := common.HexToAddress("0x000000000000000000000000000000000000dE1E")
			authorityKey, err := crypto.GenerateKey()
			suite.Require().NoError(err)
			authority := crypto.PubkeyToAddress(authorityKey.PublicKey)

			vmdb := suite.StateDB()
			vmdb.SetCode(failingTarget, []byte{0xfe}, 0)
			suite.Require().NoError(vmdb.Commit())

			msg := suite.buildSetCodeTx(failingTarget, authorityKey, delegate, 0, 100000)
			res, err := suite.App.EvmKeeper.EthereumTx(suite.Ctx, msg)
			suite.Require().NoError(err)
			suite.Require().True(res.Failed())
			suite.Require().NotEmpty(res.VmError)

			suite.requireSetCodeAuthorizationConsumed(authority, delegate, 1)
		})
	}
```
