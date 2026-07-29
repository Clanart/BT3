[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/erc20/keeper/msg_server.go (L133-140)
```go
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return nil, err
	}

	// Send minted coins to the receiver
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, receiver, coins); err != nil {
		return nil, err
	}
```

**File:** precompiles/common/balance_handler.go (L68-71)
```go
func (bh *BalanceHandler) AfterBalanceChange(ctx sdk.Context, stateDB *statedb.StateDB) error {
	events := ctx.EventManager().Events()

	for _, event := range events[bh.prevEventsLen:] {
```

**File:** precompiles/erc20/query.go (L175-175)
```go
	balance := p.BankKeeper.SpendableCoin(ctx, account.Bytes(), p.tokenPair.Denom)
```

**File:** x/vm/keeper/statedb.go (L33-34)
```go
	acct.Balance = k.SpendableCoin(ctx, addr)
	return acct
```
