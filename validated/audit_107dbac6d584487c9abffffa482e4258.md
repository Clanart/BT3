### Title
Post-SELFDESTRUCT Non-EVM-Native Token Orphaning — Permanent Loss of Cosmos Bank Funds (`File: x/evm/statedb/statedb.go`)

### Summary

When a contract is selfdestructed, Ethermint's `StateDB.Commit()` only burns the EVM-denom balance of the destroyed address. Non-EVM-native tokens (IBC, CosmWasm bridge tokens) held by the contract are explicitly skipped and permanently orphaned in the Cosmos bank module after `DeleteAccount` removes the `EthAccount`. Any unprivileged EVM transaction that selfdestructs a contract holding such tokens causes their irrecoverable loss.

### Finding Description

The analog vulnerability class from the external report is: **irreversible state destruction that causes permanent loss of funds** — after a contract is destroyed, funds sent to (or held at) the address are lost forever because the code/account no longer exists to handle them.

In Ethermint, `StateDB.Commit()` handles selfdestructed objects as follows: [1](#0-0) 

The critical limitation is explicitly acknowledged in the code comment at lines 813–815: [2](#0-1) 

The flow is:
1. `SelfDestruct()` burns the EVM-denom balance at destruction time via `SubBalance`.
2. `Commit()` burns any EVM-denom balance that arrived post-destruction (the fix).
3. `DeleteAccount()` removes the `EthAccount` from the auth module.
4. **Non-EVM-native tokens (IBC, CosmWasm bridge) are never touched** — they remain as orphaned bank balances at the now-accountless address. [3](#0-2) 

After `DeleteAccount` removes the `EthAccount`, the address has no controlling account in the auth module. For contract addresses created by other contracts (e.g., via `CREATE2`), there is no externally-owned private key that maps to the address, so the orphaned IBC tokens are permanently unrecoverable.

The `AddBalance` / `SubBalance` path for the EVM denom goes through `keeper.AddBalance` / `keeper.SubBalance`: [4](#0-3) 

These only operate on `s.evmDenom`. No equivalent drain exists for other denominations held in the bank module at the destroyed address.

### Impact Explanation

**High — valid user funds mis-accounted via EVM state transition.**

A contract holding IBC tokens (e.g., a DeFi vault that received `uatom` or `uosmo` via IBC transfer) that is selfdestructed will permanently lose those tokens. The `DeleteAccount` call removes the `EthAccount`, leaving the IBC tokens as an orphaned bank balance with no account to authorize their movement. This is a direct permanent loss of Cosmos bank funds triggered by a standard EVM state transition.

### Likelihood Explanation

**Moderate.** Requires two conditions:
1. A contract holds non-EVM-native tokens (IBC tokens are common in Cosmos ecosystems).
2. The contract is selfdestructed (under EIP-6780, this requires creation and destruction in the same transaction, which is a standard pattern for flash-loan-style or factory contracts).

Both conditions are realistic in production Ethermint deployments integrated with IBC.

### Recommendation

In `StateDB.Commit()`, after burning the EVM-denom balance and before calling `DeleteAccount`, enumerate **all** bank balances held by the destroyed address and burn or transfer them:

```go
// Drain ALL denominations, not just evmDenom
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct non-evm balance")
    }
}
```

Alternatively, transfer non-EVM-native tokens to the SELFDESTRUCT beneficiary address (matching Ethereum's behavior of forwarding all value to the beneficiary).

### Proof of Concept

1. Deploy a contract `Vault` that holds IBC `uatom` tokens (received via IBC transfer to its Cosmos address).
2. In a single transaction, deploy a factory contract via `CREATE2`, have the factory call `Vault.selfdestruct(beneficiary)`.
3. Under EIP-6780, the contract is destroyed (created and destroyed in same tx).
4. `StateDB.Commit()` burns the EVM-denom balance but skips `uatom`.
5. `DeleteAccount` removes the `EthAccount`.
6. `uatom` tokens remain at the address with no controlling account — permanently orphaned.
7. `eth_getBalance` returns 0 (EVM denom burned), but `bank.Balance(addr, "uatom")` returns the original amount with no way to spend it. [5](#0-4)

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

**File:** x/evm/keeper/statedb.go (L80-101)
```go
func (k *Keeper) AddBalance(ctx sdk.Context, addr sdk.AccAddress, coin sdk.Coin) (uint256.Int, error) {
	coins := sdk.NewCoins(coin)
	prevBalance := k.GetBalance(ctx, addr, coin.Denom)
	if err := k.bankKeeper.MintCoins(ctx, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.ModuleName, addr, coins); err != nil {
		return uint256.Int{}, err
	}
	return prevBalance, nil
}

func (k *Keeper) SubBalance(ctx sdk.Context, addr sdk.AccAddress, coin sdk.Coin) (uint256.Int, error) {
	coins := sdk.NewCoins(coin)
	prevBalance := k.GetBalance(ctx, addr, coin.Denom)
	if err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, addr, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	if err := k.bankKeeper.BurnCoins(ctx, types.ModuleName, coins); err != nil {
		return uint256.Int{}, err
	}
	return prevBalance, nil
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
