### Title
Batched `MsgEthereumTx` Ante Handler Reads Stale Balance Per-Message, Permitting Under-Funded Value-Transfer Transactions to Commit - (File: `ante/eth.go`)

### Summary

`VerifyEthAccount` and `CheckEthCanTransfer` in `ante/eth.go` iterate over every `MsgEthereumTx` in a batched Cosmos transaction and read the sender's balance independently for each message, without tracking the cumulative cost already committed by prior messages in the same batch. Because the Cosmos SDK allows multiple `MsgEthereumTx` messages in a single transaction envelope (confirmed by `test_batch_tx` and ADR-003), an attacker can craft a batch whose total `value` component exceeds their balance while each individual message passes the per-message balance check. The ante handler admits the entire batch; the EVM execution then fails for the under-funded messages, but fees are already deducted for all of them. This is the direct Ethermint analog of the external report's "pre-execution hook reads stale state that does not account for prior calls in the same batch" vulnerability class.

### Finding Description

**`VerifyEthAccount` — stale balance check (runs before fee deductions)** [1](#0-0) 

For every message in the batch the function calls `evmKeeper.GetBalance(ctx, from, evmDenom)` and then `keeper.CheckSenderBalance(balance, ethTx)`. The balance is fetched from the Cosmos bank store, which has not been modified by any prior iteration of the loop (fee deductions have not happened yet). `CheckSenderBalance` compares the **same unchanged balance** against `tx.Cost() = gasLimit * gasPrice + value` for every message independently. [2](#0-1) 

**`CheckEthGasConsume` — fee deductions are cumulative but value is excluded** [3](#0-2) 

`DeductTxCostsFromUserBalance` is called per message and does reduce the bank balance by `gasLimit * effectiveGasPrice` (fees only). After this decorator the balance is `B − Σ fees`. Value transfers are **not** deducted here; they happen during EVM execution.

**`CheckEthCanTransfer` — stale post-fee balance check** [4](#0-3) 

This decorator runs after `CheckEthGasConsume`. `canTransfer` reads the post-fee balance from the bank store. For a batch of N messages each with value V it reads the **same** post-fee balance for every message:

```
msg1: (B − N·G) ≥ V  ✓
msg2: (B − N·G) ≥ V  ✓   ← same balance, not updated
...
msgN: (B − N·G) ≥ V  ✓
``` [5](#0-4) 

The value transfers that would reduce the balance only occur during EVM execution, which runs after all ante handler decorators have completed.

**Batch transactions are a supported, reachable path** [6](#0-5) 

### Impact Explanation

The ante handler admits a batch whose **total** cost (`Σ (gasLimit·gasPrice + value)`) exceeds the sender's balance, as long as each individual message's cost is ≤ the balance. All fees are deducted upfront for every message. During EVM execution the first message that successfully transfers value drains the balance; subsequent messages with `value > 0` fail with `VmError` (insufficient funds). Those failed messages are committed to the block with `status = 0`, and the sender's fees for them are permanently consumed. This constitutes:

- An **ante handler bug that permits invalid transactions to commit**: messages that cannot be executed successfully (because the balance is insufficient after accounting for prior messages in the batch) pass all ante handler checks and are included in the block.
- **Valid user funds/fees mis-accounted**: fees are charged for EVM transactions that were structurally guaranteed to fail at the time the ante handler ran.

### Likelihood Explanation

Batched EVM transactions are a documented, tested feature of Ethermint (ADR-003, `test_batch_tx`). Any user who submits a batch of `MsgEthereumTx` messages with non-zero `value` fields whose cumulative total exceeds their balance triggers this path. No privileged role, governance action, or external dependency is required. The attacker controls the batch contents entirely through a normal transaction submission.

### Recommendation

1. **Cumulative balance tracking in `VerifyEthAccount`**: maintain a per-sender running total of `tx.Cost()` across the message loop and compare against the balance once, or subtract each message's cost from a local copy of the balance before checking the next message.

2. **Cumulative value tracking in `CheckEthCanTransfer`**: similarly accumulate the `value` field across messages from the same sender and compare the total against the post-fee balance.

3. **Alternatively**, reject batched `MsgEthereumTx` transactions at the ante handler level (as ADR-003 recommends discouraging them) to eliminate the entire class of stale-state issues in batch processing.

### Proof of Concept

```
Sender balance B = G + V   (G = gasLimit * gasPrice, V = value per message)
Batch: [msg1(value=V, gas=G), msg2(value=V, gas=G)]

VerifyEthAccount:
  msg1: GetBalance() = B = G+V ≥ G+V  ✓
  msg2: GetBalance() = B = G+V ≥ G+V  ✓  ← stale, not updated

CheckEthGasConsume:
  msg1: DeductTxCosts(G) → balance = V
  msg2: DeductTxCosts(G) → balance = V−G  (succeeds if V > G)

CheckEthCanTransfer:
  msg1: GetBalance() = V−G ≥ V  ✓  (if G ≈ 0)
  msg2: GetBalance() = V−G ≥ V  ✓  ← same stale balance

→ Both messages admitted to block.

EVM execution:
  msg1: transfers V → balance = −G  (fails, VmError)
  msg2: transfers V → balance = −G  (fails, VmError)

Result: sender paid 2G in fees; both value transfers failed.
        Ante handler incorrectly admitted msg2 as valid.
```

The root cause is at `ante/eth.go` lines 101–104 (`VerifyEthAccount`) and lines 242–249 (`CheckEthCanTransfer`), both of which read `evmKeeper.GetBalance` / `canTransfer` against the same unchanged store state for every message in the batch. [7](#0-6) [8](#0-7)

### Citations

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

**File:** ante/eth.go (L138-186)
```go
	for _, msg := range tx.GetMsgs() {
		msgEthTx, ok := msg.(*evmtypes.MsgEthereumTx)
		if !ok {
			return ctx, errorsmod.Wrapf(errortypes.ErrUnknownRequest, "invalid message type %T, expected %T", msg, (*evmtypes.MsgEthereumTx)(nil))
		}

		priority := evmtypes.GetTxPriority(msgEthTx, baseFee)

		if priority < minPriority {
			minPriority = priority
		}

		// We can't trust the tx gas limit, because we'll refund the unused gas.
		gasLimit := msgEthTx.GetGas()
		if gasWanted > math.MaxInt64-gasLimit {
			return ctx, fmt.Errorf("gasWanted(%d) + gasLimit(%d) overflow", gasWanted, gasLimit)
		}
		gasWanted += gasLimit
		if gasWanted > blockGasLimit {
			return ctx, errorsmod.Wrapf(
				errortypes.ErrOutOfGas,
				"tx gas (%d) exceeds block gas limit (%d)",
				gasWanted,
				blockGasLimit,
			)
		}
		// user balance is already checked during CheckTx so there's no need to
		// verify it again during ReCheckTx
		if ctx.IsReCheckTx() {
			continue
		}

		fees, err := keeper.VerifyFee(msgEthTx, evmDenom, baseFee, rules, ctx.IsCheckTx())
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to verify the fees")
		}

		err = evmKeeper.DeductTxCostsFromUserBalance(ctx, fees, common.BytesToAddress(msgEthTx.From))
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to deduct transaction costs from user balance")
		}

		events = append(events,
			sdk.NewEvent(
				sdk.EventTypeTx,
				sdk.NewAttribute(sdk.AttributeKeyFee, fees.String()),
			),
		)
	}
```

**File:** ante/eth.go (L213-253)
```go
	for _, msg := range tx.GetMsgs() {
		msgEthTx, ok := msg.(*evmtypes.MsgEthereumTx)
		if !ok {
			return errorsmod.Wrapf(errortypes.ErrUnknownRequest, "invalid message type %T, expected %T", msg, (*evmtypes.MsgEthereumTx)(nil))
		}

		tx := msgEthTx.AsTransaction()
		if rules.IsLondon {
			if baseFee == nil {
				return errorsmod.Wrap(
					evmtypes.ErrInvalidBaseFee,
					"base fee is supported but evm block context value is nil",
				)
			}
			if tx.GasFeeCap().Cmp(baseFee) < 0 {
				return errorsmod.Wrapf(
					errortypes.ErrInsufficientFee,
					"max fee per gas less than block base fee (%s < %s)",
					tx.GasFeeCap(), baseFee,
				)
			}
		}
		value := tx.Value()
		if value == nil || value.Sign() == -1 {
			return fmt.Errorf("value (%s) must be positive", value)
		}
		from := common.BytesToAddress(msgEthTx.From)
		// check that caller has enough balance to cover asset transfer for **topmost** call
		// NOTE: here the gas consumed is from the context with the infinite gas meter
		if value.Sign() > 0 && !canTransfer(ctx, evmKeeper, evmParams.EvmDenom, from, value) {
			return errorsmod.Wrapf(
				errortypes.ErrInsufficientFunds,
				"failed to transfer %s from address %s using the EVM block context transfer function",
				value,
				from,
			)
		}
	}

	return nil
}
```

**File:** ante/eth.go (L255-259)
```go
// canTransfer adapted the core.CanTransfer from go-ethereum
func canTransfer(ctx sdk.Context, evmKeeper interfaces.EVMKeeper, denom string, from common.Address, amount *big.Int) bool {
	balance := evmKeeper.GetBalance(ctx, sdk.AccAddress(from.Bytes()), denom)
	return balance.ToBig().Cmp(amount) >= 0
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

**File:** docs/architecture/adr-003-batch-evm-transactions.md (L19-36)
```markdown
### Cosmos SDK Batch Transactions

The Cosmos SDK allows multiple messages (transactions) to be included in a single transaction envelope. This feature enables atomic execution of multiple operations and can reduce transaction overhead. In Ethermint, this means multiple `MsgEthereumTx` messages can be batched together in a single Cosmos transaction.

### Ethereum Nonce Model

In Ethereum, each account maintains a nonce that:

1. Prevents replay attacks by ensuring transaction ordering
2. Determines contract addresses for CREATE operations via `keccak256(rlp([sender, nonce]))`
3. Increments sequentially: one increment per transaction, plus additional increments for nested contract creations

### The Ante Handler Problem

In Cosmos SDK, the ante handler processes transactions before execution. Ethermint's ante handler increments account nonces for **all messages in a batch upfront** before EVM execution begins. For a batch with 3 messages from the same sender:

- **Before ante handler:** Account nonce = N
- **After ante handler:** Account nonce = N+3
```
