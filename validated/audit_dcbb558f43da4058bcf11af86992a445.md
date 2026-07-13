### Title
EIP-7702 Delegated CALL with Nested CREATE Bumps Nonce Beyond Ante-Handler Expectation, Enabling Fee-Free Block Stuffing - (File: x/evm/keeper/state_transition.go)

### Summary

In Ethermint's EIP-7702 implementation, a CALL transaction targeting a delegated EOA can execute nested `CREATE` opcodes that increment the sender's nonce by more than 1. The ante handler only pre-increments the nonce by 1 per message and does not simulate EVM execution. `PrepareProposal` therefore accepts a follow-up transaction with the "old" nonce (N+1), but `FinalizeBlock` rejects it after the delegated CALL has already bumped the nonce to N+1+k. The rejected transaction occupies block space without paying any fees, enabling a virtually free block-stuffing attack.

### Finding Description

**Root cause — CALL branch of `ApplyMessageWithConfig`:**

In `x/evm/keeper/state_transition.go`, the `contractCreation` branch explicitly saves the pre-execution nonce, resets it to `msg.Nonce`, calls `evm.Create()`, and then reconciles the final nonce: [1](#0-0) 

The `else` (CALL) branch has **no such reconciliation**. It relies on the ante handler's single +1 increment and calls `evm.Call()` directly: [2](#0-1) 

When the delegated code (installed via EIP-7702) executes a `CREATE` opcode, go-ethereum's EVM calls `stateDB.SetNonce()` to bump the sender's nonce. That write is committed to the Cosmos account store when the stateDB is committed, leaving the account sequence at N+1+k instead of N+1.

**Ante handler — only increments by 1:**

`CheckAndSetEthSenderNonce` in `ante/eth.go` increments the Cosmos account sequence by exactly 1 per message and does not simulate EVM execution: [3](#0-2) 

**Authorization nonce bump compounds the issue:**

`setAuthorizationDelegation` in `x/evm/keeper/set_code_authorizations.go` also bumps the authority's nonce by 1 per valid authorization before `evm.Call()` runs: [4](#0-3) 

This means a self-delegating EIP-7702 transaction with one authorization and one nested CREATE bumps the nonce by 3 (ante +1, auth +1, CREATE +1), while the ante handler only accounts for 1.

**PrepareProposal vs FinalizeBlock divergence:**

`PrepareProposal` uses `baseapp.NewDefaultProposalHandler`, which runs the ante handler sequentially over candidate transactions in a simulated context: [5](#0-4) 

During `PrepareProposal`, only the ante handler runs (nonce N → N+1). The EVM execution that bumps the nonce to N+1+k happens later in `FinalizeBlock`. Therefore a follow-up transaction with nonce N+1 passes `PrepareProposal` but fails the ante handler during `FinalizeBlock`.

### Impact Explanation

A transaction that fails the ante handler during `FinalizeBlock` is included in the committed block (it was already accepted by `PrepareProposal` and `ProcessProposal`) but its state changes — including fee deduction — are reverted. The transaction consumes block space without paying any gas fees. An attacker who controls an EIP-7702 delegation to a factory contract can repeatedly pair a nonce-bumping delegated CALL with a cheap follow-up transaction, filling blocks at near-zero cost and denying block space to legitimate users.

This matches the allowed High impact: *"mempool or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

- Requires only an unprivileged Ethereum account with an EIP-7702 delegation (type-4 transaction, available to any user on a Prague-enabled chain).
- The factory contract that performs `CREATE` can be deployed by the attacker.
- No special privileges, governance access, or validator collusion required.
- The attack is repeatable every block.

### Recommendation

After `evm.Call()` returns in the CALL branch of `ApplyMessageWithConfig`, read the post-execution nonce from the stateDB and reconcile it against the ante-handler-incremented value, mirroring the logic already present in the `contractCreation` branch:

```go
} else {
    // ... apply authorizations ...
    preCallNonce := stateDB.GetNonce(sender)
    ret, leftoverGas, vmErr = evm.Call(sender, *msg.To, msg.Data, leftoverGas, uint256.MustFromBig(msg.Value))
    // Reconcile any extra nonce increments from nested CREATEs in delegated code.
    postCallNonce := stateDB.GetNonce(sender)
    if postCallNonce > preCallNonce {
        // extra increments already applied; nothing to do
    } else if postCallNonce < preCallNonce {
        // evm.Call did not increment (normal CALL); ensure ante-handler increment is preserved
        stateDB.SetNonce(sender, preCallNonce, tracing.NonceChangeUnspecified)
    }
}
```

Alternatively, expose the final stateDB nonce to the mempool/proposal layer so that `PrepareProposal` can account for nonce bumps from EVM execution when validating subsequent transactions in the same block.

### Proof of Concept

1. Deploy a factory contract `F` that exposes a `deploy(bytes)` function executing `CREATE`.
2. EOA `A` (nonce N) sends an EIP-7702 type-4 transaction delegating to `F` (auth nonce = N+1). After this tx, `A`'s nonce = N+2.
3. `A` sends tx1 (nonce N+2): a regular CALL to `A` itself (delegated to `F`), calling `deploy(bytecode)`. This triggers one `CREATE`, bumping `A`'s nonce to N+3 after `FinalizeBlock`.
4. Simultaneously, `A` sends tx2 (nonce N+3... wait, let me redo):

Simpler scenario (pre-delegation already set):

1. EOA `A` already has EIP-7702 delegation to factory `F`. Current nonce = N.
2. `A` submits tx1 (nonce N): CALL to `A` (delegated to `F`), which executes `CREATE`. Ante handler: N → N+1. EVM execution: N+1 → N+2 (CREATE). stateDB commit: account sequence = N+2.
3. `A` submits tx2 (nonce N+1): any valid transaction.
4. During `PrepareProposal`: tx1 ante handler sets sequence to N+1; tx2 ante handler sees N+1 → passes. Both included in block.
5. During `FinalizeBlock`: tx1 executes fully, sequence becomes N+2. tx2 ante handler sees expected N+2, got N+1 → **fails**. tx2 is in the block but pays no fees.
6. Repeat every block to stuff blocks at zero cost.

The integration test `test_single_tx_create` in `tests/integration_tests/test_nonce_evm_call.py` already documents that a single delegated CALL with an embedded CREATE produces a final nonce of `deploy_nonce + 2`, confirming the double-increment: [6](#0-5)

### Citations

**File:** x/evm/keeper/state_transition.go (L468-482)
```go
	if contractCreation {
		oldNonce := stateDB.GetNonce(sender)
		// take over the nonce management from evm:
		// - reset sender's nonce to msg.Nonce() before calling evm.
		// nonce is preincremented in antehandler, so we need to reset it here.
		// this is to ensure the nonce is correct for the creation of the contract.
		stateDB.SetNonce(sender, msg.Nonce, tracing.NonceChangeUnspecified)
		ret, _, leftoverGas, vmErr = evm.Create(sender, msg.Data, leftoverGas, uint256.MustFromBig(msg.Value))
		// evm.Create() increments nonce from msg.Nonce to (msg.Nonce + 1 + nestedCreates)
		// We need: oldNonce + nestedCreates
		afterCreateNonce := stateDB.GetNonce(sender)
		nestedCreates := afterCreateNonce - msg.Nonce - 1
		// setting nonce to the updated value is essential
		// as there may be subsequent evm call messages which doesn't increase nonce
		stateDB.SetNonce(sender, oldNonce+nestedCreates, tracing.NonceChangeUnspecified)
```

**File:** x/evm/keeper/state_transition.go (L483-517)
```go
	} else {
		if msg.SetCodeAuthorizations != nil {
			// Track validated authorizations together with the authority recovered
			// during validation, so the durable replay below can reuse it.
			type validAuth struct {
				auth      ethtypes.SetCodeAuthorization
				authority common.Address
			}
			var validAuths []validAuth
			for _, auth := range msg.SetCodeAuthorizations {
				// Note errors are ignored, we simply skip invalid authorizations here.
				authority, err := k.applyAuthorization(&auth, stateDB)
				if err != nil {
					k.Logger(ctx).Debug("failed to apply authorization", "error", err, "authorization", auth)
					continue
				}
				validAuths = append(validAuths, validAuth{auth: auth, authority: authority})
			}

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
		}
		// based on geth, nonce should be preincremented before evm call execution
		// which is already done on the antehandler
		ret, leftoverGas, vmErr = evm.Call(sender, *msg.To, msg.Data, leftoverGas, uint256.MustFromBig(msg.Value))
```

**File:** ante/eth.go (L285-326)
```go
		expectedNonce := acc.GetSequence()
		txNonce := tx.Nonce()
		fromStr := from.String()

		// if flag is set, we bypass nonce all check verification
		if !unsafeUnOrderedTx {
			ex := nonceCache.Exists(fromStr, txNonce)
			// to support tx replacement, we check if the transaction nonce exists in the cache and if yes we skip
			// nonce verification, and we don't set the sequence
			// We allow skip verification only during CheckTx to keep sequence safe during the execution.
			if ctx.IsCheckTx() && !ctx.IsReCheckTx() && ex {
				continue
			}

			// nonce verification, the sequence needs to be in order
			if txNonce != expectedNonce {
				// delete in case of recheck tx
				if ex {
					nonceCache.Delete(fromStr, txNonce)
				}
				return nil, errorsmod.Wrapf(
					errortypes.ErrInvalidSequence,
					"invalid nonce; got %d, expected %d", txNonce, expectedNonce,
				)
			}

			if ctx.IsCheckTx() {
				if !ctx.IsReCheckTx() {
					pending = append(pending, cache.TxNonce{Address: fromStr, Nonce: txNonce})
				}
			} else if ex {
				// delete in case of deliver tx
				nonceCache.Delete(fromStr, txNonce)
			}
		}

		// increase sequence of sender
		if err := acc.SetSequence(expectedNonce + 1); err != nil {
			return nil, errorsmod.Wrapf(err, "failed to set sequence to %d", acc.GetSequence()+1)
		}

		ak.SetAccount(ctx, acc)
```

**File:** x/evm/keeper/set_code_authorizations.go (L74-84)
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
```

**File:** evmd/mempool.go (L31-36)
```go
		handler := baseapp.NewDefaultProposalHandler(mp, app)

		app.SetMempool(mp)
		app.SetPrepareProposal(handler.PrepareProposalHandler())
		app.SetProcessProposal(handler.ProcessProposalHandler())
	}
```

**File:** tests/integration_tests/test_nonce_evm_call.py (L209-216)
```python
    # Verify delegator's final nonce
    final_nonce = w3.eth.get_transaction_count(delegator.address)
    expected_final_nonce = deploy_nonce + 2  # +1 for tx, +1 for create embedded in call
    print(f"✓ Delegator final nonce: {final_nonce} (expected: {expected_final_nonce})")

    assert (
        final_nonce == expected_final_nonce
    ), f"Delegator nonce should be {expected_final_nonce}, got {final_nonce}"
```
