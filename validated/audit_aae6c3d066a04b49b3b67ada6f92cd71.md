### Title
Non-EVM-Native Token Balances (IBC, CosmWasm Bridge) Permanently Orphaned on Contract Self-Destruct — (`File: x/evm/statedb/statedb.go`)

### Summary
When an EVM contract that holds non-EVM-native Cosmos bank tokens (e.g., IBC-transferred assets, CosmWasm bridge tokens) executes `SELFDESTRUCT`, the `StateDB.Commit()` path burns only the EVM-denom balance and then calls `DeleteAccount()` to remove the auth account. Non-EVM-native bank balances at the destroyed address are never drained. The auth account is gone, the EVM account is gone, but the bank module still holds those tokens at the now-deleted address — permanently orphaned with no on-chain recovery path.

### Finding Description

In `StateDB.Commit()`, the self-destruct branch explicitly limits its cleanup to the EVM denom:

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

`DeleteAccount()` in the keeper removes auth metadata and EVM storage, but never touches bank balances for any denom:

```go
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    // clear storage
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool { ... })
    // remove auth account
    k.accountKeeper.RemoveAccount(ctx, acct)
    return nil
}
``` [2](#0-1) 

After `writeCache()` commits, the state is:
- Auth account: **deleted**
- EVM storage: **deleted**
- EVM-denom bank balance: **burned** ✓
- Non-EVM-native bank balances (IBC atoms, USDC, etc.): **still present at the address, unspendable**

The Cosmos bank module stores balances keyed by address independently of the auth module. Once the auth account is removed, there is no on-chain mechanism to spend or recover those orphaned bank balances. For contracts deployed via `CREATE` (non-deterministic address), the address can never be reused, making the loss permanent. For `CREATE2` contracts, a redeployment at the same address by a different party could allow a third party to claim the orphaned tokens — a theft vector.

### Impact Explanation

Any non-EVM-native Cosmos bank tokens held by a self-destructed EVM contract are permanently locked in the bank module with no recovery path. This is a direct loss of user funds through a standard EVM state transition. The impact matches: **"EVM state transition bug that permits valid user funds/fees to be mis-accounted."**

Concrete loss scenario:
1. A DeFi contract on an Ethermint chain receives IBC-transferred USDC (or any non-EVM-denom) via an IBC precompile or native bank send.
2. The contract owner calls `SELFDESTRUCT` (or a bug in the contract triggers it).
3. `StateDB.Commit()` burns the EVM-denom balance and deletes the auth account.
4. The IBC USDC remains in the bank module at the now-deleted address — permanently inaccessible.

For `CREATE2` contracts, an additional theft vector exists: an attacker who knows the factory address, salt, and init code can redeploy a new contract at the same address and drain the orphaned non-EVM-native tokens that belonged to the original contract's users.

### Likelihood Explanation

Ethermint chains routinely enable IBC and expose precompiles (e.g., IBC transfer precompile, bank precompile as documented in `docs/precompile_creation_guide.md`) that allow EVM contracts to hold non-EVM-native Cosmos bank tokens. Any contract that:
- Receives IBC tokens via a precompile call or native bank send, **and**
- Executes `SELFDESTRUCT` (intentionally or via a vulnerability)

will trigger this loss. The entry path is a standard unprivileged EVM transaction. No privileged role, governance action, or validator compromise is required.

### Recommendation

In `StateDB.Commit()`, before calling `DeleteAccount()`, iterate over **all** bank balances held by the self-destructed address (not just the EVM denom) and burn or redirect them. The Cosmos SDK `bankKeeper.GetAllBalances(ctx, cosmosAddr)` returns all denominations. Each non-zero balance should be burned via `bankKeeper.BurnCoins` (after sending to a module account) or transferred to a designated recovery address (e.g., the community pool or a governance-controlled address), so that no funds are silently orphaned.

### Proof of Concept

The root cause is explicitly acknowledged in the production code at `x/evm/statedb/statedb.go` lines 813–815:

```
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
``` [3](#0-2) 

Step-by-step trigger:

1. Deploy a contract `C` on an Ethermint chain that has IBC enabled.
2. IBC-transfer 1000 USDC to the Cosmos address corresponding to `C`'s EVM address (i.e., `sdk.AccAddress(C.Bytes())`). The bank module now holds 1000 USDC at that address.
3. Call a function on `C` that executes `SELFDESTRUCT`.
4. `StateDB.SelfDestruct()` burns the EVM-denom balance via `SubBalance`.
5. `StateDB.Commit()` burns any remaining EVM-denom balance and calls `DeleteAccount()`, which removes the auth account and EVM storage.
6. Query `bankKeeper.GetBalance(ctx, sdk.AccAddress(C.Bytes()), "usdc")` — it returns 1000 USDC.
7. Query `accountKeeper.GetAccount(ctx, sdk.AccAddress(C.Bytes()))` — it returns `nil`.
8. The 1000 USDC is permanently orphaned: no auth account exists to authorize a spend, and no on-chain mechanism exists to recover it.

The `DeleteAccount` keeper function confirms it never touches non-EVM-denom bank balances: [4](#0-3)

### Citations

**File:** x/evm/statedb/statedb.go (L813-825)
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
