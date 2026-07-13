### Title
Nonce Reset in Batched EVM CREATE Transactions Causes Contract Address Collisions and Fee Loss - (`x/evm/keeper/state_transition.go`, `ante/eth.go`)

### Summary

`CheckAndSetEthSenderNonce` pre-increments the Cosmos account sequence for **all** messages in a batch upfront, while `ApplyMessageWithConfig` resets the stateDB nonce back to `msg.Nonce` for each CREATE message. When an earlier CREATE in the batch performs nested CREATEs that consume nonces expected by later messages, the later CREATE messages collide on the same contract address, fail with `ErrContractAddressCollision`, and the user is charged fees for the failed execution.

### Finding Description

`CheckAndSetEthSenderNonce` iterates over every message in the batch and unconditionally increments the Cosmos account sequence by 1 per message: [1](#0-0) 

For a batch of 2 CREATE messages from sender with nonce N, after the ante handler the Cosmos account sequence is N+2.

In `ApplyMessageWithConfig`, the CREATE branch reads the current stateDB nonce (`oldNonce = N+2`), resets it to `msg.Nonce` (N), calls `evm.Create()`, then reconciles: [2](#0-1) 

If msg0's constructor performs one nested CREATE, it consumes nonces N and N+1. The reconciliation sets the final nonce to `oldNonce + nestedCreates = N+3`.

When msg1 executes, its stateDB loads nonce N+3, resets to `msg.Nonce = N+1`, and calls `evm.Create()`. The target address `keccak256(rlp([sender, N+1]))` was already created by msg0's nested CREATE — `evm.Create()` returns `ErrContractAddressCollision`. The transaction is included in the block with status=0 and fees are charged.

The project's own ADR-003 explicitly documents this collision: [3](#0-2) 

The ADR marks the status as "PROPOSED, Implemented" but does not gate or prevent batched EVM CREATE transactions — the code path remains fully reachable.

### Impact Explanation

A user submitting a valid batch of `MsgEthereumTx` CREATE messages where an earlier message performs nested CREATEs will have subsequent messages fail with `ErrContractAddressCollision`. The user:
- Pays full gas fees for the failed messages
- Has their deployment left in a partially-executed, inconsistent state
- Must reconstruct and resubmit the remaining transactions individually

This matches the allowed High impact: **EVM state transition bug that permits valid user funds/fees to be mis-accounted** — the user's funds are consumed for transactions that fail due to a protocol-level nonce collision, not due to any user error.

### Likelihood Explanation

Any unprivileged user can trigger this by submitting a Cosmos SDK multi-message transaction containing multiple `MsgEthereumTx` CREATE messages where at least one earlier message performs a nested CREATE (e.g., a factory contract pattern). This is a realistic and common deployment pattern. No special privileges, governance access, or validator cooperation is required.

### Recommendation

**Short Term**: In `CheckAndSetEthSenderNonce`, do not pre-increment the Cosmos account sequence for CREATE messages. Instead, defer the sequence increment to after EVM execution (post-`ApplyMessageWithConfig`), so the stateDB nonce at execution time equals `msg.Nonce` without requiring a reset. This eliminates the need for the reset-and-reconcile logic entirely.

**Long Term**: Enforce at the ante handler level that a Cosmos transaction containing multiple `MsgEthereumTx` CREATE messages from the same sender is rejected, or add a protocol-level guard that prevents batched EVM CREATE transactions until a safe nonce management scheme is implemented.

### Proof of Concept

1. Sender has Cosmos account sequence N.
2. Sender submits a Cosmos batch transaction with 2 `MsgEthereumTx` messages:
   - **msg0**: `To = nil` (CREATE), `Nonce = N`. Constructor bytecode performs one nested `CREATE` opcode.
   - **msg1**: `To = nil` (CREATE), `Nonce = N+1`. Simple deployment, no nested creates.
3. **Ante handler** (`CheckAndSetEthSenderNonce`): increments sequence for msg0 → N+1, then for msg1 → N+2.
4. **msg0 execution** (`ApplyMessageWithConfig`):
   - `oldNonce = stateDB.GetNonce(sender) = N+2`
   - Reset: `stateDB.SetNonce(sender, N)`
   - `evm.Create()`: deploys contract at `addr(sender, N)`, nonce → N+1; nested CREATE deploys at `addr(sender, N+1)`, nonce → N+2
   - `afterCreateNonce = N+2`, `nestedCreates = 1`
   - Reconcile: `stateDB.SetNonce(sender, N+2+1 = N+3)`
   - Commits successfully.
5. **msg1 execution** (`ApplyMessageWithConfig`):
   - `oldNonce = stateDB.GetNonce(sender) = N+3`
   - Reset: `stateDB.SetNonce(sender, N+1)`
   - `evm.Create()`: attempts to deploy at `addr(sender, N+1)` → **`ErrContractAddressCollision`** (already created by msg0's nested CREATE)
   - `vmErr != nil`, transaction included with `status=0`
   - User is charged gas fees for msg1.
6. **Result**: msg1's contract is never deployed; user loses fees; deployment is left in a broken intermediate state requiring manual recovery.

### Citations

**File:** ante/eth.go (L269-327)
```go
	for _, msg := range tx.GetMsgs() {
		msgEthTx, ok := msg.(*evmtypes.MsgEthereumTx)
		if !ok {
			return nil, errorsmod.Wrapf(errortypes.ErrUnknownRequest, "invalid message type %T, expected %T", msg, (*evmtypes.MsgEthereumTx)(nil))
		}

		tx := msgEthTx.AsTransaction()

		from := msgEthTx.GetFrom()
		acc := accountGetter(from)
		if acc == nil {
			return nil, errorsmod.Wrapf(
				errortypes.ErrUnknownAddress,
				"account %s is nil", common.BytesToAddress(from.Bytes()),
			)
		}
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
	}
```

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

**File:** docs/architecture/adr-003-batch-evm-transactions.md (L106-115)
```markdown
### Example 1: Batch EVM CREATE Transactions with Nested Creates

When multiple CREATE transactions are batched, nonce resets can cause address collisions:

| Transaction | Initial Nonce | Contracts Created | Nonces Used | Issue |
|-------------|---------------|-------------------|-------------|-------|
| tx0 (msg.Nonce=0) | 0 | 2 (1 parent + 1 nested) | 0, 1 | Reset to 0, creates at nonce 0 and 1 |
| tx1 (msg.Nonce=1) | 1 (after tx0) | 1 | 1 | Reset to 1, creates at nonce 1 (collision!) |

**Result**: Both transactions attempt to create a contract at an address derived from nonce 1, causing a conflict.
```
