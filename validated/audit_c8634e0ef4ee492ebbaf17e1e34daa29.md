### Title
Non-EVM-Denom (IBC/Bridge) Token Balances Permanently Orphaned After SELFDESTRUCT, Enabling CREATE2 Theft - (`x/evm/statedb/statedb.go`)

---

### Summary

When a contract holding non-EVM-native tokens (IBC tokens, CosmWasm bridge tokens) executes `SELFDESTRUCT`, `StateDB.Commit()` burns only the EVM-denom balance and removes the auth account, but explicitly leaves all other Cosmos bank balances untouched. The code itself acknowledges this gap. The orphaned tokens are permanently inaccessible through normal signing — but if the original contract was deployed via `CREATE2`, an attacker can redeploy a new contract at the identical address, causing the bank module to associate the surviving non-EVM-denom balance with the new account, enabling unauthorized theft of those Cosmos bank funds.

---

### Finding Description

In `StateDB.Commit()`, the self-destruct path is:

```go
if obj.selfDestructed {
    cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
    cacheCtx, writeCache := s.origCtx.CacheContext()
    // Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
    // bridge) held by the destroyed address are not drained and may remain as
    // orphaned bank balances.
    if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
        ...
        s.keeper.SubBalance(cacheCtx, cosmosAddr, coin)
    }
    s.keeper.DeleteAccount(cacheCtx, obj.Address())
    writeCache()
}
``` [1](#0-0) 

`DeleteAccount` in the keeper removes EVM storage slots and the auth account, but never touches the bank module for any denom other than the EVM denom:

```go
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    // clear storage
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
        k.SetState(ctx, addr, key, nil)
        return true
    })
    // remove auth account
    k.accountKeeper.RemoveAccount(ctx, acct)
    ...
}
``` [2](#0-1) 

After `DeleteAccount`, the Cosmos bank module still holds any IBC or bridge token balances at the destroyed address. Because the auth account is gone, no one can sign a transaction from that address — the tokens are orphaned. However, the bank module's balance record persists independently of the auth account.

The critical escalation: if the original contract was deployed via `CREATE2`, the deployment parameters (factory address + salt + init code) are fully public on-chain. An attacker can call `CREATE2` with the same parameters to redeploy a contract at the identical address. The EVM keeper's `SetAccount` call during contract creation re-establishes an auth account at that address. The bank module then sees an existing non-EVM-denom balance at that address, which the new contract can drain via `ExecuteNativeAction` or a native precompile call. [3](#0-2) 

---

### Impact Explanation

Any non-EVM-denom tokens (IBC-transferred assets, CosmWasm bridge tokens) held by a self-destructed contract are permanently removed from the auth account system but remain in the Cosmos bank module. In the worst case — `CREATE2`-deployed contracts — an attacker can redeploy at the same address and steal those Cosmos bank funds. This is unauthorized theft of Cosmos bank funds through Ethermint stateDB commit logic, matching the Critical allowed impact.

---

### Likelihood Explanation

Cosmos chains running Ethermint commonly support IBC. Contracts that act as escrows, liquidity pools, or bridges routinely hold IBC-denominated tokens. `SELFDESTRUCT` is a standard EVM opcode reachable by any unprivileged transaction. `CREATE2` deployment parameters are always public on-chain. The attacker does not need any privileged role: they only need to observe the original deployment transaction and replay the `CREATE2` call after the victim contract self-destructs.

---

### Recommendation

Before calling `DeleteAccount`, enumerate all bank module balances held by the destroyed address across all denoms and burn or redirect them. The Cosmos SDK `bankKeeper` exposes `GetAllBalances(ctx, addr)` for this purpose. The burn should be wrapped in the same `CacheContext` as the existing EVM-denom burn and `DeleteAccount` call so that a partial failure rolls back atomically.

---

### Proof of Concept

1. Attacker deploys `VictimEscrow` via `CREATE2(factory, salt, initCode)`. The contract accepts IBC-USDC transfers.
2. Users send IBC-USDC to `VictimEscrow`. The bank module records the balance at `childAddr`.
3. `VictimEscrow.destroy()` is called; `SELFDESTRUCT` fires. `StateDB.Commit()` burns the EVM-denom balance and calls `DeleteAccount`, removing the auth account. IBC-USDC balance remains in the bank module at `childAddr`.
4. Attacker calls `CREATE2(factory, salt, initCode)` again. The EVM creates a new auth account at `childAddr` via `SetAccount`.
5. The new contract at `childAddr` calls a native bank precompile (or `ExecuteNativeAction`) to transfer the IBC-USDC balance to the attacker's address.
6. Attacker receives the IBC-USDC that was held by the original contract.

The root cause — only `s.evmDenom` is burned while all other bank balances are silently skipped — is confirmed by the explicit code comment at `x/evm/statedb/statedb.go` lines 813–815. [4](#0-3) [5](#0-4)

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
