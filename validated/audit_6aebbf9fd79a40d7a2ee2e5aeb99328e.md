### Title
Non-EVM-Denom (IBC/CosmWasm) Tokens Held by Self-Destructed Contracts Become Permanently Orphaned — (`File: x/evm/statedb/statedb.go`)

### Summary
When a contract self-destructs, `StateDB.Commit()` only burns the EVM-native denom balance. Non-EVM-native Cosmos bank tokens (IBC assets, CosmWasm bridge tokens, etc.) held by the destroyed address are explicitly skipped and left as orphaned bank balances. Because `DeleteAccount` removes the auth account record, no private key or contract code remains to sign transactions from that address, and there is no governance sweep mechanism. The tokens are permanently locked.

### Finding Description
In `StateDB.Commit()`, the self-destruct handling path reads:

```go
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
``` [1](#0-0) 

Only `s.evmDenom` is queried and burned. Any other Cosmos bank denomination held by the contract address is never touched. `DeleteAccount` then removes the auth account record:

```go
// remove auth account
k.accountKeeper.RemoveAccount(ctx, acct)
``` [2](#0-1) 

After `RemoveAccount`, the address has no associated account object, no code, and no private key. The orphaned non-EVM-denom bank balances are permanently inaccessible. There is no governance sweep, no upgrade path in the statedb layer, and no recovery mechanism.

### Impact Explanation
Any non-EVM-denom Cosmos bank tokens (e.g., IBC-transferred `uatom`, `uosmo`, or CosmWasm bridge tokens) held by a contract address at the time of self-destruct are permanently locked. The bank module still records them as belonging to the now-deleted address, but they can never be spent, transferred, or recovered. This is a permanent, irreversible loss of Cosmos bank funds caused directly by the EVM state transition path in `StateDB.Commit()`.

This maps to the High allowed impact: **"EVM state transition… bug that permits… valid user funds/fees to be mis-accounted."** Cosmos bank funds are permanently mis-accounted — the supply is not reduced (no burn), but the funds are inaccessible forever.

### Likelihood Explanation
The entry path is fully unprivileged:
1. A user deploys a contract (standard EVM `CREATE`).
2. IBC tokens are transferred to the contract's Cosmos address (the same 20-byte address is valid in both Ethereum and Cosmos namespaces on Ethermint).
3. The contract executes `SELFDESTRUCT` (or `SELFDESTRUCT6780` within the same deployment tx).
4. `StateDB.Commit()` burns only the EVM denom and deletes the account, leaving IBC tokens orphaned.

This scenario is realistic on any Ethermint chain with IBC enabled. A contract can receive IBC tokens either intentionally (e.g., a DeFi contract that accepts multi-denom deposits) or accidentally (a user mistakenly sends IBC tokens to a contract address). In either case, a subsequent self-destruct permanently locks those tokens.

### Recommendation
In the self-destruct handling block of `StateDB.Commit()`, iterate over **all** bank denominations held by the contract address and burn or redirect them before calling `DeleteAccount`. The `bankKeeper.GetAllBalances(ctx, cosmosAddr)` call returns all denominations. Each non-EVM denom should either be burned (if the chain policy is to destroy them) or sent to a designated community pool or governance-controlled address to allow future recovery.

```go
// Drain ALL denominations, not just evmDenom
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
    }
}
```

### Proof of Concept
The codebase itself contains the explicit acknowledgment of this gap: [3](#0-2) 

> "Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm bridge) held by the destroyed address are not drained and may remain as orphaned bank balances."

Concrete steps:
1. Deploy contract `C` at address `0xABCD…`.
2. IBC-transfer 1000 `uatom` to the Cosmos address `cosmos1…` corresponding to `0xABCD…`.
3. Call `C.destroy()` which executes `SELFDESTRUCT`.
4. After the transaction commits, `keeper.GetAccount(ctx, 0xABCD…)` returns `nil` (account deleted).
5. `bankKeeper.GetBalance(ctx, cosmos1…, "uatom")` returns `1000` — the tokens are still in the bank but permanently inaccessible.

The `DeleteAccount` function's own comment confirms the design gap: **"NOTE: balance should be cleared separately"** — but the caller (`StateDB.Commit`) only clears the EVM denom, not all balances. [4](#0-3)

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

**File:** x/evm/keeper/statedb.go (L189-194)
```go
// DeleteAccount handles contract's suicide call:
// - remove code
// - remove states
// - remove auth account
//
// NOTE: balance should be cleared separately
```

**File:** x/evm/keeper/statedb.go (L195-222)
```go
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
