### Title
Non-EVM-native Token Balances Permanently Orphaned on SELFDESTRUCT — (`x/evm/statedb/statedb.go`)

### Summary
When a contract self-destructs, `StateDB.Commit()` only burns the EVM-native denomination balance. Non-EVM-native tokens (IBC, CosmWasm bridge tokens) held by the destroyed address are never drained and remain as permanently orphaned bank balances after the auth account is deleted. No one can ever recover them.

### Finding Description

In `StateDB.Commit()`, the self-destruct path burns the EVM denom balance and then calls `DeleteAccount` to remove the auth account and contract storage. The code explicitly acknowledges — but does not fix — the case where the destroyed address holds non-EVM-native tokens: [1](#0-0) 

```go
cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
cacheCtx, writeCache := s.origCtx.CacheContext()
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
    ...
}
if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
    return errorsmod.Wrap(err, "failed to delete account")
}
writeCache()
```

`DeleteAccount` removes the auth account and contract storage, but never touches the bank module balances for any denomination other than the EVM denom: [2](#0-1) 

After `DeleteAccount` completes, the Cosmos auth account is gone. Any IBC or bridge tokens still held at that address in the bank module have no owner — the bank module retains the balance entry, but there is no account that can sign a transaction to spend it. The tokens are permanently locked.

The analog to the Union Finance report is exact: just as removing an adapter without checking its remaining supply locks funds in that adapter, self-destructing a contract without draining all token denominations locks those tokens at the destroyed address forever.

### Impact Explanation

**High.** Any non-EVM-native tokens (IBC vouchers, CosmWasm bridge tokens, or any `sdk.Coin` denomination other than the EVM denom) held by a self-destructed contract address are permanently removed from circulation. The bank module total supply is not reduced (the coins still exist), but they are inaccessible — the auth account that could authorize spending them has been deleted by `DeleteAccount`. This constitutes permanent, irrecoverable mis-accounting of valid user funds through the EVM state transition path.

### Likelihood Explanation

**Medium.** The scenario requires non-EVM-native tokens to be present at a contract address at the time of self-destruction. This is reachable through:
- IBC transfers sent directly to a contract address (valid on Cosmos chains)
- Stateful precompiles (e.g., a bank precompile) that transfer IBC tokens into a contract
- CosmWasm bridge interactions that deposit tokens to an EVM contract address

An attacker can deploy a contract, attract IBC token deposits (e.g., by acting as a liquidity pool or bridge), and then trigger `SELFDESTRUCT` (or EIP-6780 destruction within the same transaction) to permanently lock all non-EVM-native deposits. No privileged role is required; any unprivileged EVM transaction can trigger this path.

### Recommendation

Before calling `DeleteAccount`, iterate over **all** bank module balances held by the destroyed address — not just the EVM denom — and burn or redirect each denomination. The `bankKeeper.GetAllBalances(ctx, cosmosAddr)` call returns all coins; each should be burned (sent to the module account and burned) or returned to a designated recovery address before the auth account is removed.

```go
// Drain ALL denominations, not just evmDenom
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
if !allBalances.IsZero() {
    if err := s.keeper.BurnCoins(cacheCtx, cosmosAddr, allBalances); err != nil {
        return errorsmod.Wrap(err, "failed to burn all post-selfdestruct balances")
    }
}
```

### Proof of Concept

1. Deploy a contract `C` that accepts IBC token deposits via a bank precompile or direct IBC transfer.
2. Send 1000 `ibc/XXXX` tokens to `C`'s address via an IBC transfer (valid Cosmos operation).
3. In a single EVM transaction, deploy a child contract via `CREATE2`, immediately call `SELFDESTRUCT` on it (EIP-6780 applies since it was created in the same tx), and forward `msg.value` to the child — or simply call `SELFDESTRUCT` on `C` directly.
4. `StateDB.Commit()` runs: it burns the EVM denom balance of `C` and calls `DeleteAccount(C)`.
5. After commit: `keeper.GetAccount(ctx, C)` returns `nil` (auth account deleted). [3](#0-2) 
6. Query `bankKeeper.GetBalance(ctx, C, "ibc/XXXX")` — it returns 1000. The tokens are still in the bank module at address `C`, but there is no auth account to authorize spending them. They are permanently locked.

The existing test `TestSelfDestructPostDestructionBalanceBurned` only verifies the EVM denom case and does not cover non-EVM-native denominations, confirming the gap is untested and unfixed. [4](#0-3)

### Citations

**File:** x/evm/statedb/statedb.go (L811-825)
```go
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** x/evm/keeper/statedb.go (L189-222)
```go
// DeleteAccount handles contract's suicide call:
// - remove code
// - remove states
// - remove auth account
//
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum accounts (contracts) can be selfdestructed
	_, ok := acct.(ethermint.EthAccountI)
	if !ok {
		return errorsmod.Wrapf(types.ErrInvalidAccount, "type %T, address %s", acct, addr)
	}

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		k.SetState(ctx, addr, key, nil)
		return true
	})

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.debugLog(ctx, "account suicided",
		"ethereum-address", addr,
		"cosmos-address", cosmosAddr,
	)

	return nil
```

**File:** x/evm/statedb/statedb_test.go (L1053-1091)
```go
// TestSelfDestructPostDestructionBalanceBurned verifies that any balance credited to a
// self-destructed address within the same transaction is burned at commit time rather
// than left as an orphaned bank balance recoverable by recreating the address.
func (suite *StateDBTestSuite) TestSelfDestructPostDestructionBalanceBurned() {
	raw, ctx, keeper := setupTestEnv(suite.T())

	// Setup: create a contract account with initial balance and code.
	db := statedb.New(ctx, keeper, emptyTxConfig)
	db.CreateAccount(address)
	db.CreateContract(address)
	db.SetCode(address, []byte("contract code"), 0)
	db.AddBalance(address, uint256.NewInt(100), tracing.BalanceChangeTransfer)
	suite.Require().NoError(db.Commit())

	ctx, keeper = newTestKeeper(suite.T(), raw)

	// Phase 1: Self-destruct the contract; its initial balance (100) must be burned.
	db = statedb.New(ctx, keeper, emptyTxConfig)
	db.SelfDestruct(address)
	suite.Require().True(db.HasSelfDestructed(address))
	suite.Require().Equal(uint256.NewInt(0), db.GetBalance(address))

	// Phase 2: Send value to the already-destroyed address in the same transaction.
	// This simulates a CALL with value to a self-destructed contract.
	postDestructValue := uint256.NewInt(500)
	db.AddBalance(address, postDestructValue, tracing.BalanceChangeTransfer)
	suite.Require().Equal(postDestructValue, db.GetBalance(address))

	suite.Require().NoError(db.Commit())

	// After commit: account metadata must be gone.
	ctx, keeper = newTestKeeper(suite.T(), raw)
	suite.Require().Nil(keeper.GetAccount(ctx, address))

	// The post-destruction balance must be burned (zero), not preserved.
	cosmosAddr := sdk.AccAddress(address.Bytes())
	balance := keeper.GetBalance(ctx, cosmosAddr, "uphoton")
	suite.Require().True(balance.IsZero(), "post-selfdestruct balance must be burned at commit")
}
```
