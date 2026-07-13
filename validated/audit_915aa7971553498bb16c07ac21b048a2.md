### Title
Unchecked Bank-Transfer Errors in `StateDB` Silently Continue EVM Execution and Bypass Gas Refund — (File: `x/evm/statedb/statedb.go`)

---

### Summary

`StateDB.Transfer`, `AddBalance`, `SubBalance`, and `SetBalance` each call `ExecuteNativeAction` and, on failure, store the error in `s.err` without halting EVM execution. The go-ethereum `vm.StateDB` interface does not allow these methods to return errors, so the EVM continues executing as if the bank operation succeeded. The error is only surfaced at `Commit()` time, where it is returned as a **cosmos-level error** (not an EVM-level `VmError`). This causes `ApplyTransaction` to return early, skipping the `RefundGas` call entirely, so the sender is charged the full `gasLimit × gasPrice` with no refund for unused gas.

---

### Finding Description

**Root cause — silent error accumulation in `StateDB`:**

`Transfer`, `AddBalance`, `SubBalance`, and `SetBalance` all follow the same pattern:

```go
// x/evm/statedb/statedb.go:445-449
if err := s.ExecuteNativeAction(common.Address{}, nil, func(ctx sdk.Context) error {
    return s.keeper.Transfer(ctx, senderAddr, recipientAddr, coins)
}); err != nil {
    s.err = err   // ← error stored, EVM execution continues
}
``` [1](#0-0) [2](#0-1) [3](#0-2) 

The EVM is unaware of the failure and continues executing contract code. The native cache layer is rolled back by `ExecuteNativeAction` on failure, so the bank state is consistent, but the EVM's in-memory execution proceeds under the false assumption that the transfer succeeded.

**Error propagation — cosmos-level failure, not EVM VmError:**

`Commit()` checks `s.err` first and aborts:

```go
// x/evm/statedb/statedb.go:753-756
if s.err != nil {
    return s.err
}
``` [4](#0-3) 

In `ApplyMessageWithConfig`, only `ErrStateConflict` is promoted to a VmError; all other `Commit()` failures — including `s.err` from a failed bank transfer — are returned as cosmos-level errors:

```go
// x/evm/keeper/state_transition.go:602-623
if commit {
    if err := stateDB.Commit(); err != nil {
        if errors.Is(err, statedb.ErrStateConflict) {
            return &types.EVMResult{..., VmError: statedb.ErrStateConflict.Error()}, nil
        }
        return nil, errorsmod.Wrap(err, "failed to commit stateDB")  // ← cosmos error
    }
}
``` [5](#0-4) 

**Gas refund bypass:**

`ApplyTransaction` calls `RefundGas` only when `ApplyMessageWithConfig` returns `(result, nil)`. A cosmos-level error causes an early return, skipping the refund entirely:

```go
// x/evm/keeper/state_transition.go:194-254
res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
if err != nil {
    return nil, errorsmod.Wrap(err, "failed to apply ethereum core message")
    // RefundGas is never reached
}
// ...
if err = k.RefundGas(ctx, msg, leftoverGas, cfg.Params.EvmDenom); err != nil { ... }
``` [6](#0-5) 

The ante handler already deducted `gasLimit × gasPrice` from the sender. Because `RefundGas` is skipped, the sender is charged the full gas limit instead of only the gas consumed.

---

### Impact Explanation

The sender pays `gasLimit × gasPrice` (deducted by the ante handler) but receives no refund for `(gasLimit − gasUsed) × gasPrice` of unused gas. This is a direct mis-accounting of valid user funds/fees. The magnitude equals the unused gas multiplied by the effective gas price — for a transaction with a high gas limit and early failure, this can be substantial. Additionally, the transaction fails at the cosmos level rather than the EVM level (status=0), which breaks Ethereum receipt semantics and may confuse wallets and indexers.

---

### Likelihood Explanation

The trigger condition is a bank `SendCoins` (or `MintCoins`/`SendCoinsFromModuleToAccount`) failure during EVM execution. This occurs when:

1. **A contract sends ETH to a Cosmos module account address** (e.g., the fee collector, staking module). Module accounts are blocked addresses; `bankKeeper.SendCoins` rejects them. The `CanTransfer` check in the ante handler and EVM block context only verifies the sender's balance — it does not check whether the recipient is blocked. Any contract that routes value to such an address (deliberately or accidentally) triggers this path.
2. **The EVM denom is send-disabled** via governance, causing every value-bearing EVM transaction to fail at the cosmos level.

Case 1 is reachable by any unprivileged user deploying or calling a contract.

---

### Recommendation

Treat bank-operation failures in `StateDB` as EVM-level failures rather than cosmos-level errors. In `ApplyMessageWithConfig`, extend the `ErrStateConflict` special-case to cover all `s.err`-originated `Commit()` failures by returning a `VmError` result (with `gasUsed` set to the full gas limit, matching Ethereum semantics for failed transactions) instead of a cosmos-level error. This ensures `RefundGas` is called on the normal path and gas accounting remains correct.

Alternatively, ensure `RefundGas` is called unconditionally in `ApplyTransaction` even when `ApplyMessageWithConfig` returns a cosmos-level error, so the sender is not overcharged.

---

### Proof of Concept

1. Identify a known Cosmos module account address on the target chain (e.g., the fee collector: `cosmos17xpfvakm2amg962yls6f84z3kell8c5lserqta` mapped to its EVM hex address).
2. Deploy a Solidity contract:
   ```solidity
   contract Trigger {
       function fire(address target) external payable {
           payable(target).transfer(msg.value);
       }
   }
   ```
3. Call `fire{value: 1}(moduleAccountEvmAddress)` with a high gas limit (e.g., 500,000 gas).
4. Internally: `statedb.Transfer` calls `keeper.Transfer` → `bankKeeper.SendCoins` → fails (blocked address) → `s.err` is set.
5. EVM continues; `Commit()` returns `s.err`; `ApplyMessageWithConfig` returns a cosmos-level error.
6. `ApplyTransaction` returns early; `RefundGas` is never called.
7. Observe: the sender's balance is reduced by `500000 × gasPrice` (full gas limit), not by `actualGasUsed × gasPrice`. The difference is the overcharge. [7](#0-6) [8](#0-7)

### Citations

**File:** x/evm/statedb/statedb.go (L433-450)
```go
// Transfer from one account to another
func (s *StateDB) Transfer(sender, recipient common.Address, amount *uint256.Int) {
	if amount.Sign() == 0 {
		return
	}
	if amount.Sign() < 0 {
		panic("negative amount")
	}

	coins := sdk.NewCoins(sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigIntMut(amount.ToBig())))
	senderAddr := sdk.AccAddress(sender.Bytes())
	recipientAddr := sdk.AccAddress(recipient.Bytes())
	if err := s.ExecuteNativeAction(common.Address{}, nil, func(ctx sdk.Context) error {
		return s.keeper.Transfer(ctx, senderAddr, recipientAddr, coins)
	}); err != nil {
		s.err = err
	}
}
```

**File:** x/evm/statedb/statedb.go (L462-468)
```go
	if err := s.ExecuteNativeAction(common.Address{}, nil, func(ctx sdk.Context) error {
		var addErr error
		balance, addErr = s.keeper.AddBalance(ctx, sdk.AccAddress(addr.Bytes()), coin)
		return addErr
	}); err != nil {
		s.err = err
	}
```

**File:** x/evm/statedb/statedb.go (L483-491)
```go
	if err := s.ExecuteNativeAction(common.Address{}, nil, func(ctx sdk.Context) error {
		var subErr error
		balance, subErr = s.keeper.SubBalance(ctx, sdk.AccAddress(addr.Bytes()), coin)
		return subErr
	}); err != nil {
		s.err = err
	}

	return balance
```

**File:** x/evm/statedb/statedb.go (L748-756)
```go
func (s *StateDB) Commit() error {
	if s.committed {
		return errors.New("statedb already committed")
	}
	s.committed = true
	// if there's any errors during the execution, abort
	if s.err != nil {
		return s.err
	}
```

**File:** x/evm/keeper/state_transition.go (L194-254)
```go
	res, err := k.ApplyMessageWithConfig(tmpCtx, msg, cfg, true)
	if err != nil {
		return nil, errorsmod.Wrap(err, "failed to apply ethereum core message")
	}

	logs := types.LogsToEthereum(res.Logs)

	// Compute block bloom filter
	if len(logs) > 0 {
		bloom := ethtypes.Bloom{}
		for _, log := range logs {
			bloom.Add(log.Address.Bytes())
			for _, topic := range log.Topics {
				bloom.Add(topic[:])
			}
		}
		k.SetTxBloom(tmpCtx, bloom.Big())
	}

	var contractAddr common.Address
	if msg.To == nil {
		contractAddr = crypto.CreateAddress(msg.From, msg.Nonce)
	}

	receipt := &ethtypes.Receipt{
		Type:            ethTx.Type(),
		PostState:       nil, // TODO: intermediate state root
		Logs:            logs,
		TxHash:          cfg.TxConfig.TxHash,
		ContractAddress: contractAddr,
		GasUsed:         res.GasUsed,
		BlockHash:       cfg.TxConfig.BlockHash,
		BlockNumber:     cfg.BlockNumber,
	}

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
```

**File:** x/evm/keeper/state_transition.go (L602-623)
```go
	if commit {
		if err := stateDB.Commit(); err != nil {
			// A state conflict between the outer EVM and a nested native action is an
			// EVM-level failure: surface it as a VmError so the transaction is included
			// in the block with status=0 rather than rejected at the cosmos message level.
			// All other commit errors (infrastructure failures) remain cosmos-level errors.
			//
			// Note: estimateGas and eth_call do not hit this path because commit is
			// false for simulations, so they will succeed even when a real execution
			// would produce a state conflict.
			if errors.Is(err, statedb.ErrStateConflict) {
				return &types.EVMResult{
					GasUsed:          gasUsed,
					VmError:          statedb.ErrStateConflict.Error(),
					Hash:             cfg.TxConfig.TxHash.Hex(),
					BlockHash:        ctx.HeaderHash(),
					ExecutionGasUsed: temporaryGasUsed,
				}, nil
			}

			return nil, errorsmod.Wrap(err, "failed to commit stateDB")
		}
```
