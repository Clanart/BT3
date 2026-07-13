### Title
`VerifyEthAccount` Balance Check Does Not Account for Cumulative Cost of Prior Messages in Batch Transactions - (File: `ante/eth.go`)

### Summary

`VerifyEthAccount` checks each `MsgEthereumTx` in a batch independently against the sender's full on-chain balance, without tracking the cumulative cost already committed by prior messages in the same batch. This mirrors the external report's pattern exactly: a validity check reads a raw balance without accounting for already-processed operations, allowing the check to pass when it should fail.

### Finding Description

The Ethermint ante handler for EVM transactions (`newEthAnteHandler` in `evmd/ante/handler_options.go`) executes three sequential balance-related checks:

1. **`VerifyEthAccount`** (line 131) — checks `balance >= tx.Cost()` (= `gasLimit * gasPrice + value`) for each message
2. **`CheckEthCanTransfer`** (line 135) — checks `balance >= value` for each message
3. **`CheckEthGasConsume`** (line 139) — **actually deducts** fees (`gasLimit * effectiveGasPrice`) from the bank store for each message iteratively [1](#0-0) 

Inside `VerifyEthAccount`, the loop reads the same raw on-chain balance for every message in the batch:

```go
for _, msg := range tx.GetMsgs() {
    // ...
    balance := evmKeeper.GetBalance(ctx, from, evmDenom)          // same balance every iteration
    if err := keeper.CheckSenderBalance(..., balance, ethTx); err != nil {
        return err
    }
}
``` [2](#0-1) 

`CheckSenderBalance` compares the full balance against `tx.Cost()` = `gasLimit * gasPrice + value`: [3](#0-2) 

Because no deductions occur inside `VerifyEthAccount`, every message in the batch is validated against the **same pre-deduction balance**. The cumulative cost of prior messages is never subtracted.

`CheckEthGasConsume` does deduct fees iteratively (so it catches total-fee overruns), but it only deducts the **fee portion** (`gasLimit * effectiveGasPrice`), not the **value portion**: [4](#0-3) 

This leaves a gap: if the total value across all messages exceeds the balance remaining after fee deduction, the ante handler still passes, but the EVM execution of later messages will fail with insufficient funds.

**Concrete scenario** (batch of 2 messages from the same sender, balance = 100 wei):

| Step | msg1 (fee=10, value=60) | msg2 (fee=10, value=60) | Balance |
|------|------------------------|------------------------|---------|
| `VerifyEthAccount` | 100 ≥ 70 ✓ | 100 ≥ 70 ✓ (same balance) | 100 |
| `CheckEthCanTransfer` | 100 ≥ 60 ✓ | 100 ≥ 60 ✓ (same balance) | 100 |
| `CheckEthGasConsume` | deduct 10 | deduct 10 | 80 |
| EVM msg1 | transfer 60 ✓ | — | 20 |
| EVM msg2 | — | transfer 60 **FAILS** | 20 |

Total cost = 140 > 100, but the ante handler passes. The second message is included in the block with `status=0` (failed), and both fees are permanently deducted.

### Impact Explanation

The ante handler admits a batch transaction whose total cost exceeds the sender's balance. This is an **invalid transaction admission** bug: the ante handler's role is to reject such transactions before block inclusion. Instead, the transaction is committed to the block with later messages failing at EVM level, and the sender's fees are mis-accounted (fees deducted for messages that were guaranteed to fail). This matches the allowed High impact: *"ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

### Likelihood Explanation

Batch EVM transactions (multiple `MsgEthereumTx` in a single Cosmos SDK transaction envelope) are a supported feature, demonstrated by the existing `test_batch_tx` integration test and the `build_batch_tx` utility. The attack requires no special privileges — any unprivileged user can construct and submit a batch transaction via `eth_sendRawTransaction` or the Cosmos broadcast endpoint. The condition (each individual message cost ≤ balance, but total cost > balance) is straightforward to engineer. Likelihood is **Medium**.

### Recommendation

Track the cumulative cost already committed by prior messages within the `VerifyEthAccount` loop. Maintain a running `cumulativeCost` per sender address and check `balance >= cumulativeCost + tx.Cost()` at each iteration, updating `cumulativeCost` after each passing check. The same fix should be applied to `CheckEthCanTransfer` for the value portion. Alternatively, compute the total cost across all messages for each sender before the loop and perform a single aggregate balance check.

### Proof of Concept

1. Sender `A` has balance = 100 wei.
2. Construct a Cosmos batch transaction containing two `MsgEthereumTx`:
   - `msg1`: `gasLimit=10, gasPrice=1` (fee=10), `value=60` → `cost=70`
   - `msg2`: `gasLimit=10, gasPrice=1` (fee=10), `value=60` → `cost=70`
   - Total cost = 140 > 100
3. Submit via `eth_sendRawTransaction` or Cosmos broadcast.
4. **`VerifyEthAccount`**: both messages pass (100 ≥ 70 each, same balance read twice).
5. **`CheckEthCanTransfer`**: both messages pass (100 ≥ 60 each, same balance read twice).
6. **`CheckEthGasConsume`**: deducts 10+10=20 in fees; balance becomes 80. Passes.
7. Ante handler returns success. Transaction is included in the block.
8. EVM executes `msg1`: transfers 60 wei. Balance = 20.
9. EVM executes `msg2`: attempts to transfer 60 wei. Fails (only 20 available). `status=0`.
10. Both fees (20 total) are permanently deducted. The batch — whose total cost (140) exceeded the sender's balance (100) — was admitted by the ante handler. [5](#0-4) [4](#0-3) [1](#0-0)

### Citations

**File:** evmd/ante/handler_options.go (L131-145)
```go
		if err := evmante.VerifyEthAccount(ctx, tx, options.EvmKeeper, evmDenom, accountGetter, rules); err != nil {
			return ctx, err
		}

		if err := evmante.CheckEthCanTransfer(ctx, tx, baseFee, rules, options.EvmKeeper, evmParams); err != nil {
			return ctx, err
		}

		ctx, err = evmante.CheckEthGasConsume(
			ctx, tx, rules, options.EvmKeeper,
			baseFee, evmDenom,
		)
		if err != nil {
			return ctx, err
		}
```

**File:** ante/eth.go (L76-106)
```go
	for _, msg := range tx.GetMsgs() {
		msgEthTx, ok := msg.(*evmtypes.MsgEthereumTx)
		if !ok {
			return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "invalid message type %T, expected %T", msg, (*evmtypes.MsgEthereumTx)(nil))
		}

		ethTx := msgEthTx.AsTransaction()

		// sender address should be in the tx cache from the previous AnteHandle call
		from := msgEthTx.GetFrom()
		if from.Empty() {
			return errorsmod.Wrap(errortypes.ErrInvalidAddress, "from address cannot be empty")
		}

		// check whether the sender address is EOA
		acct := statedb.NewAccountFromSdkAccount(accountGetter(from))

		if !rules.IsPrague {
			if acct.IsContract() {
				fromAddr := common.BytesToAddress(from)
				return errorsmod.Wrapf(errortypes.ErrInvalidType,
					"the sender is not EOA: address %s, codeHash <%s>", fromAddr, acct.CodeHash)
			}
		}

		balance := evmKeeper.GetBalance(ctx, from, evmDenom)
		if err := keeper.CheckSenderBalance(sdkmath.NewIntFromBigIntMut(balance.ToBig()), ethTx); err != nil {
			return errorsmod.Wrap(err, "failed to check sender balance")
		}
	}
	return nil
```

**File:** ante/eth.go (L170-178)
```go
		fees, err := keeper.VerifyFee(msgEthTx, evmDenom, baseFee, rules, ctx.IsCheckTx())
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to verify the fees")
		}

		err = evmKeeper.DeductTxCostsFromUserBalance(ctx, fees, common.BytesToAddress(msgEthTx.From))
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to deduct transaction costs from user balance")
		}
```

**File:** x/evm/keeper/utils.go (L184-203)
```go
func CheckSenderBalance(
	balance sdkmath.Int,
	tx *ethtypes.Transaction,
) error {
	cost := tx.Cost()

	if cost.Sign() < 0 {
		return errorsmod.Wrapf(
			errortypes.ErrInvalidCoins,
			"tx cost (%s) is negative and invalid", cost,
		)
	}

	if balance.IsNegative() || balance.BigInt().Cmp(cost) < 0 {
		return errorsmod.Wrapf(
			errortypes.ErrInsufficientFunds,
			"sender balance < tx cost (%s < %s)", balance, tx.Cost(),
		)
	}
	return nil
```
