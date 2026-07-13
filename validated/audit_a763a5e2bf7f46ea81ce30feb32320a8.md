### Title
Non-EVM-native (IBC) Token Balances Not Burned on `SELFDESTRUCT`, Enabling Fund Recovery via CREATE2 - (File: `x/evm/statedb/statedb.go`)

---

### Summary
`StateDB.Commit()` only burns the EVM-native denom balance when processing a self-destructed account. Non-EVM-native tokens (IBC tokens, CosmWasm bridge tokens) held by the destroyed address are explicitly left as orphaned bank balances. Because `DeleteAccount` removes only the auth account record — not the bank module balances for other denoms — an attacker can recover those orphaned funds by redeploying a contract at the same address via CREATE2.

---

### Finding Description

In `StateDB.Commit()`, when flushing a self-destructed state object, the code performs two operations:

1. Burns the remaining EVM-denom balance via `keeper.SubBalance(cacheCtx, cosmosAddr, coin)`.
2. Removes the auth account and EVM storage via `keeper.DeleteAccount(cacheCtx, obj.Address())`.

The code itself documents the gap:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
    ...
}
if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
    ...
}
``` [1](#0-0) 

`DeleteAccount` in the keeper removes EVM storage and the auth account, but its own comment confirms it never touches bank balances:

```go
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    ...
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool { ... })
    k.accountKeeper.RemoveAccount(ctx, acct)
    ...
}
``` [2](#0-1) 

After `DeleteAccount` executes, the Cosmos bank module still holds any non-EVM-native token balances (e.g., IBC-transferred `uatom`, `uosmo`, etc.) at the destroyed address. These balances are keyed by address in the bank KV store and are not cleared by `RemoveAccount`.

Because CREATE2 produces deterministic addresses from `(deployer, salt, initcodeHash)`, an attacker can redeploy an identical contract at the same address in a subsequent transaction. The new contract's auth account is freshly created, but the bank module's pre-existing balance entries for that address are immediately accessible to it — via native precompile calls (`ExecuteNativeAction`) or direct bank sends.

The exploit contract pattern already exists in the test suite, confirming the attack surface is understood:

```solidity
// Attempt to recover orphaned funds by recreating the child at the same address.
// With the fix, the bank balance is already 0 so nothing is recoverable.
function redeployChild(bytes32 salt) external returns (address childAddr) { ... }
``` [3](#0-2) 

The EVM-denom case was fixed (the `SubBalance` call before `DeleteAccount`), but the fix is explicitly scoped to only the EVM denom, leaving the IBC token vector open.

---

### Impact Explanation

**High — Unauthorized theft of Cosmos bank funds (non-EVM-native tokens) through EVM SELFDESTRUCT + CREATE2.**

An attacker who controls a self-destructable contract that holds IBC tokens can:
1. Self-destruct the contract (burning only the EVM denom).
2. Redeploy at the same CREATE2 address.
3. Access the orphaned IBC token balances via the new contract.

This constitutes unauthorized transfer of Cosmos bank funds through Ethermint EVM execution, matching the allowed impact: *"Unauthorized theft … of … Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic."*

---

### Likelihood Explanation

**Medium.** The preconditions are:
- A contract must hold non-EVM-native tokens (achievable via IBC transfer to a contract address, or via a bank precompile `mint`/`transfer` call).
- The contract must be self-destructable (valid under pre-EIP-6780 rules, or under EIP-6780 if created and destroyed in the same transaction).
- The attacker must control the factory and salt to predict and reuse the CREATE2 address.

All three conditions are attacker-controllable in a single transaction sequence. No privileged role or governance action is required.

---

### Recommendation

In `StateDB.Commit()`, after burning the EVM denom, iterate over **all** bank module balances held by the destroyed address and burn them before calling `DeleteAccount`. Replace the single-denom burn with a full balance sweep:

```go
// Burn ALL bank balances, not just the EVM denom.
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
if !allBalances.IsZero() {
    if err := s.keeper.BurnCoins(cacheCtx, evmModuleName, allBalances); err != nil {
        return errorsmod.Wrap(err, "failed to burn all post-selfdestruct balances")
    }
}
```

Alternatively, expose a `GetAllBalances` method on the `statedb.Keeper` interface and drain all denoms atomically inside the same `CacheContext` that wraps `DeleteAccount`.

---

### Proof of Concept

**Setup:**
1. Deploy a factory contract `F` via a normal CREATE tx.
2. From `F`, deploy child contract `C` at address `A` via CREATE2 with salt `s`.
3. Send IBC tokens (e.g., `1000 uatom`) to address `A` via an IBC transfer or bank precompile.

**Attack:**
4. Call `C.destroy()` — triggers SELFDESTRUCT. Under EIP-6780 this is valid because `C` was created in the same tx as the destroy call (or use a pre-EIP-6780 contract).
5. `StateDB.Commit()` runs: burns `C`'s EVM denom balance, calls `DeleteAccount(A)` — auth account removed, EVM storage cleared. The `1000 uatom` bank balance at address `A` remains.
6. In a new transaction, call `F.redeployChild(s)` — CREATE2 redeploys `C'` at address `A`.
7. `C'` now has a fresh auth account at `A`. The bank module still holds `1000 uatom` at `A`.
8. `C'` calls a bank precompile or native action to transfer `1000 uatom` to the attacker — succeeds, because the bank balance was never cleared.

**Result:** The attacker recovers `1000 uatom` that should have been permanently locked/burned with the self-destructed contract.

The relevant code path confirming the gap: [4](#0-3) [5](#0-4)

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

**File:** tests/integration_tests/hardhat/contracts/SelfDestructExploit.sol (L67-78)
```text
    // Attempt to recover orphaned funds by recreating the child at the same address.
    // With the fix, the bank balance is already 0 so nothing is recoverable.
    function redeployChild(bytes32 salt) external returns (address childAddr) {
        bytes memory initCode = targetInitCode;
        assembly {
            childAddr := create2(0, add(initCode, 0x20), mload(initCode), salt)
        }
        require(childAddr != address(0), "Redeployment failed");

        // Attempt drain; with the fix applied upstream, child.balance is already 0.
        SelfDestructTarget(payable(childAddr)).drain();
    }
```
