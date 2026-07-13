### Title
`VerifyEthAccount` Balance Check Omits Cumulative Per-Message Fee Deductions in Multi-Message Transactions — (`ante/eth.go`)

### Summary

`VerifyEthAccount` reads the KV-store balance independently for every `MsgEthereumTx` in a multi-message Cosmos SDK transaction, without subtracting the cost already committed for earlier messages in the same loop. Because `CheckEthGasConsume` (which runs after `VerifyEthAccount`) deducts fees only into the virtual ObjectStore via `SendCoinsFromAccountToModuleVirtual`, those deductions are never reflected in the KV-store balance that `VerifyEthAccount` reads. The result is that all messages in a single transaction see the same original balance, allowing a sender whose balance is insufficient to cover the combined cost of all messages to pass the ante-handler balance gate for every message.

### Finding Description

`VerifyEthAccount` iterates over every message in the transaction and, for each one, fetches the sender's balance fresh from the KV store:

```go
// ante/eth.go  VerifyEthAccount
for _, msg := range tx.GetMsgs() {
    // ...
    balance := evmKeeper.GetBalance(ctx, from, evmDenom)          // KV read
    if err := keeper.CheckSenderBalance(
        sdkmath.NewIntFromBigIntMut(balance.ToBig()), ethTx); err != nil {
        return errorsmod.Wrap(err, "failed to check sender balance")
    }
}
``` [1](#0-0) 

`evmKeeper.GetBalance` delegates directly to `bankKeeper.GetBalance`, which reads the KV store:

```go
// x/evm/keeper/keeper.go  GetBalance
func (k *Keeper) GetBalance(ctx sdk.Context, addr sdk.AccAddress, denom string) uint256.Int {
    balance := k.bankKeeper.GetBalance(ctx, addr, denom).Amount.BigInt()
    return *uint256.MustFromBig(balance)
}
``` [2](#0-1) 

The ante-handler step that actually deducts fees, `CheckEthGasConsume`, runs **after** `VerifyEthAccount` and uses the virtual path:

```go
// ante/eth.go  CheckEthGasConsume
err = evmKeeper.DeductTxCostsFromUserBalance(ctx, fees, common.BytesToAddress(msgEthTx.From))
``` [3](#0-2) 

`DeductTxCostsFromUserBalance` → `DeductFees` → `SendCoinsFromAccountToModuleVirtual`, which writes only to the ObjectStore, **not** the KV store:

```go
// x/evm/keeper/utils.go  DeductFees
if err := bankKeeper.SendCoinsFromAccountToModuleVirtual(
    ctx, acc.GetAddress(), authtypes.FeeCollectorName, fees); err != nil {
``` [4](#0-3) 

Because the ObjectStore is never flushed to the KV store during ante-handler execution (it is only flushed at block commit), the KV balance read by `VerifyEthAccount` for message N+1 is identical to the balance read for message N. No "already-committed" cost is subtracted. This is the direct analog of the missing `balance.unsettledAmount` in the UsdPool report: the virtually-deducted fees are the "unsettled" component that is absent from every subsequent balance check within the same transaction.

`CheckEthCanTransfer` has the same structural defect — it also reads the KV balance independently per message without accumulating prior deductions:

```go
// ante/eth.go  canTransfer
func canTransfer(...) bool {
    balance := evmKeeper.GetBalance(ctx, sdk.AccAddress(from.Bytes()), denom)
    return balance.ToBig().Cmp(amount) >= 0
}
``` [5](#0-4) 

### Impact Explanation

A sender with balance `B` can craft a single Cosmos SDK transaction containing `N` `MsgEthereumTx` messages, each with individual cost `C < B` but with combined cost `N × C > B`. Every message passes `VerifyEthAccount` because each reads the same stale KV balance `B`. `CheckEthGasConsume` then virtually deducts `N × C` from the ObjectStore. When the ObjectStore is flushed at block commit, the cumulative virtual deduction exceeds the actual KV balance, causing fee mis-accounting. Additionally, the ante handler admits the transaction to the block even though the sender cannot cover all messages, permitting invalid transactions to commit — a High-severity impact under the allowed scope ("ante handler bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted").

### Likelihood Explanation

A Cosmos SDK transaction wrapping multiple `MsgEthereumTx` messages is a valid, unprivileged submission path — both `VerifyEthAccount` and `CheckEthGasConsume` explicitly loop over `tx.GetMsgs()`, confirming the runtime expects this case. No special privileges, governance actions, or validator collusion are required. Any user can construct such a transaction via the standard `eth_sendRawTransaction` / broadcast path.

### Recommendation

Accumulate the cost of each message within `VerifyEthAccount` and subtract it from the running balance before checking the next message:

```go
func VerifyEthAccount(...) error {
    // track cumulative cost per sender within this tx
    spent := make(map[string]*big.Int)
    for _, msg := range tx.GetMsgs() {
        // ...
        from := msgEthTx.GetFrom()
        balance := evmKeeper.GetBalance(ctx, from, evmDenom).ToBig()
        alreadySpent := spent[string(from)]
        if alreadySpent == nil { alreadySpent = new(big.Int) }
        effectiveBalance := new(big.Int).Sub(balance, alreadySpent)
        if err := keeper.CheckSenderBalance(
            sdkmath.NewIntFromBigInt(effectiveBalance), ethTx); err != nil {
            return err
        }
        spent[string(from)] = new(big.Int).Add(alreadySpent, ethTx.Cost())
    }
    return nil
}
```

Apply the same cumulative-deduction pattern in `CheckEthCanTransfer`.

### Proof of Concept

1. Sender Alice has balance = 100 tokens.
2. Alice constructs a Cosmos SDK tx with two `MsgEthereumTx` messages, each with `gasLimit * gasPrice + value = 60`.
3. `VerifyEthAccount` runs:
   - Msg1: reads KV balance = 100; 100 ≥ 60 → **passes**.
   - Msg2: reads KV balance = 100 (unchanged); 100 ≥ 60 → **passes** (should fail: 100 − 60 = 40 < 60).
4. `CheckEthGasConsume` runs:
   - Msg1: virtually deducts 60 (ObjectStore only; KV still = 100).
   - Msg2: virtually deducts 60 (ObjectStore only; KV still = 100; total virtual deduction = 120 > 100).
5. Transaction is admitted to the block. At block commit the ObjectStore flush attempts to reconcile 120 tokens of virtual deductions against a KV balance of 100, causing fee mis-accounting. EVM execution of Msg2 will also encounter an insufficient-balance error at the `SubBalance` call, yet the transaction was already committed. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** ante/eth.go (L70-107)
```go
func VerifyEthAccount(
	ctx sdk.Context, tx sdk.Tx,
	evmKeeper interfaces.EVMKeeper, evmDenom string,
	accountGetter AccountGetter,
	rules params.Rules,
) error {
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
}
```

**File:** ante/eth.go (L124-202)
```go
func CheckEthGasConsume(
	ctx sdk.Context, tx sdk.Tx,
	rules params.Rules,
	evmKeeper interfaces.EVMKeeper,
	baseFee *big.Int,
	evmDenom string,
) (sdk.Context, error) {
	gasWanted := uint64(0)
	var events sdk.Events

	// Use the lowest priority of all the messages as the final one.
	minPriority := int64(math.MaxInt64)
	blockGasLimit := ethermint.BlockGasLimit(ctx)

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

	ctx.EventManager().EmitEvents(events)

	// Set tx GasMeter with a limit of GasWanted (i.e gas limit from the Ethereum tx).
	// The gas consumed will be then reset to the gas used by the state transition
	// in the EVM.

	// FIXME: use a custom gas configuration that doesn't add any additional gas and only
	// takes into account the gas consumed at the end of the EVM transaction.
	newCtx := ctx.
		WithGasMeter(ethermint.NewInfiniteGasMeterWithLimit(gasWanted)).
		WithPriority(minPriority)

	// we know that we have enough gas on the pool to cover the intrinsic gas
	return newCtx, nil
}
```

**File:** ante/eth.go (L256-259)
```go
func canTransfer(ctx sdk.Context, evmKeeper interfaces.EVMKeeper, denom string, from common.Address, amount *big.Int) bool {
	balance := evmKeeper.GetBalance(ctx, sdk.AccAddress(from.Bytes()), denom)
	return balance.ToBig().Cmp(amount) >= 0
}
```

**File:** x/evm/keeper/keeper.go (L297-300)
```go
func (k *Keeper) GetBalance(ctx sdk.Context, addr sdk.AccAddress, denom string) uint256.Int {
	balance := k.bankKeeper.GetBalance(ctx, addr, denom).Amount.BigInt()
	return *uint256.MustFromBig(balance)
}
```

**File:** x/evm/keeper/utils.go (L206-217)
```go
// DeductFees deducts fees from the given account.
func DeductFees(bankKeeper types.BankKeeper, ctx sdk.Context, acc sdk.AccountI, fees sdk.Coins) error {
	if !fees.IsValid() {
		return errorsmod.Wrapf(errortypes.ErrInsufficientFee, "invalid fee amount: %s", fees)
	}
	if ctx.BlockHeight() > 0 {
		if err := bankKeeper.SendCoinsFromAccountToModuleVirtual(ctx, acc.GetAddress(), authtypes.FeeCollectorName, fees); err != nil {
			return errorsmod.Wrap(errortypes.ErrInsufficientFunds, err.Error())
		}
	}
	return nil
}
```
