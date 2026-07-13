### Title
Non-EVM-Denom Cosmos Bank Balances Permanently Stranded After Contract `SelfDestruct` — (File: `x/evm/statedb/statedb.go`)

### Summary

When a contract self-destructs, `StateDB.Commit()` burns only the EVM-native denomination balance and then calls `DeleteAccount` to remove auth metadata and storage. Non-EVM-denom Cosmos bank tokens (IBC assets, bridged tokens, etc.) held by the contract address are never drained. After `DeleteAccount` removes the account from the auth module, those bank balances become permanently orphaned — no account exists to sign a recovery transaction, and no EVM code remains to move them. This is the direct Ethermint analog of the MarinateV2 bug: a "removal" operation (`DeleteAccount`) executes without first checking and clearing residual non-EVM-denom balances, leaving those funds unrecoverable.

### Finding Description

In `x/evm/statedb/statedb.go`, `Commit()` processes self-destructed accounts as follows:

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

`DeleteAccount` in `x/evm/keeper/statedb.go` clears storage slots and removes the auth account record, but explicitly does not touch bank balances:

```go
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    ...
    // clear storage
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool { ... })
    // remove auth account
    k.accountKeeper.RemoveAccount(ctx, acct)
    ...
}
``` [2](#0-1) 

After `RemoveAccount` executes, the Cosmos address has no auth account. Any non-EVM-denom bank balances (IBC tokens, wrapped assets, etc.) that were held by the contract remain in the bank module's KV store under that address, but:

- No account exists to authorize a `MsgSend` or any Cosmos transaction from that address.
- The EVM contract is deleted, so no EVM call can move those tokens.
- There is no recovery precompile or governance path to drain orphaned bank balances from a deleted address.

The funds are permanently unrecoverable.

### Impact Explanation

**High — EVM state transition bug that causes valid user funds to be permanently mis-accounted.**

Non-EVM-denom Cosmos bank tokens (e.g., IBC-transferred ATOM, USDC, or any bridged asset) held by a self-destructed contract are permanently stranded. The bank module's total supply accounting still counts these tokens as existing, but they are held by an address with no account — creating a permanent discrepancy between the bank supply and the set of reachable balances. Users who sent IBC tokens to a contract that subsequently self-destructs lose those funds with no recovery path.

### Likelihood Explanation

The trigger path is fully unprivileged:

1. An attacker deploys a contract that accepts IBC token transfers to its Cosmos address (e.g., via an IBC `MsgTransfer` targeting the contract's bech32 address).
2. The contract (or a caller) invokes `SELFDESTRUCT`.
3. `StateDB.Commit()` burns only the EVM denom and calls `DeleteAccount`.
4. IBC tokens remain as orphaned bank balances.

This can also happen accidentally: any contract that receives IBC tokens (e.g., via a precompile or a Cosmos-side transfer) and is later self-destructed will strand those tokens. The scenario is realistic on any Ethermint chain that supports IBC and EVM simultaneously.

### Recommendation

Before calling `DeleteAccount`, enumerate and drain **all** bank module denominations held by the contract address, not just the EVM denom. The fix mirrors the MarinateV2 resolution: check for residual balances of every denom before executing the removal.

```go
// Drain all bank balances, not just the EVM denom
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct non-EVM balance")
    }
}
```

Alternatively, add a `require`-equivalent guard that returns an error (surfaced as a `VmError`) if the contract holds any non-EVM-denom balance at self-destruct time, preventing the destruction until those balances are cleared by the contract logic itself.

### Proof of Concept

1. Deploy contract `C` on an Ethermint chain that supports IBC.
2. Via IBC `MsgTransfer`, send 1000 `ibc/ATOM` to `C`'s bech32 Cosmos address. The bank module records `ibc/ATOM` balance for `C`.
3. Call `C.selfDestruct()` from an EOA.
4. `ApplyMessageWithConfig` → `stateDB.Commit()` executes:
   - Burns EVM denom balance of `C` (0, since no EVM denom was held).
   - Calls `DeleteAccount(C)` → `RemoveAccount` removes `C` from auth module.
5. Query `bank.Balance(C, "ibc/ATOM")` → returns 1000. The balance exists in the bank store.
6. Attempt any recovery: no auth account for `C` → `MsgSend` from `C` is rejected. No EVM code at `C` → EVM calls revert. The 1000 `ibc/ATOM` are permanently stranded. [3](#0-2) [2](#0-1)

### Citations

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
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
