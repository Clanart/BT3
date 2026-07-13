### Title
Incomplete Post-SELFDESTRUCT Balance Burn Leaves Non-EVM-Native Cosmos Bank Tokens Recoverable via CREATE2 Redeployment — (File: x/evm/statedb/statedb.go)

---

### Summary

`StateDB.Commit()` burns only the EVM-denom balance when finalizing a self-destructed account. Non-EVM-native tokens (IBC, CosmWasm bridge denominations) held by the destroyed address are explicitly left in the Cosmos bank module. Because the EVM account is deleted, the code assumes those balances are permanently "orphaned." That assumption is wrong: a CREATE2 redeployment at the same address makes the orphaned bank balance fully accessible to the new contract, enabling an attacker to steal the preserved tokens.

---

### Finding Description

In `StateDB.Commit()`, the self-destruct branch reads:

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

The EVM-denom fix was introduced specifically to prevent the post-selfdestruct balance-preservation exploit (confirmed by `TestSelfDestructPostDestructionBalanceBurned` and the integration test `test_selfdestruct_recreated_address_cannot_recover_funds`). [2](#0-1) [3](#0-2) 

However, the fix is incomplete. The comment explicitly acknowledges that non-EVM-native denominations are **not** burned. The word "orphaned" implies the developers believe those balances are permanently inaccessible. That belief is incorrect.

**Why the assumption is wrong:** `DeleteAccount` removes the auth-module account record and EVM storage, but the Cosmos `x/bank` module stores balances independently, keyed only by the bech32 address. After `DeleteAccount`, the bank entry for non-EVM-native tokens persists. When a new contract is deployed at the same address via `CREATE2` (same deployer + same salt + same init-code hash), the new contract occupies the identical address and therefore inherits the orphaned bank balance. Any chain that exposes a bank precompile or allows `ExecuteNativeAction` inside contracts gives the new contract a direct path to transfer those tokens.

The `SelfDestructExploitFactory` test contract already demonstrates the full CREATE2-deploy → SELFDESTRUCT → redeploy cycle that makes this reachable: [4](#0-3) 

The existing integration test only asserts that `eth_getBalance` (EVM denom) is zero after redeploy; it never checks non-EVM-native denominations, so the gap is untested. [5](#0-4) 

---

### Impact Explanation

An attacker who controls a CREATE2 salt can:

1. Deploy a contract at a deterministic address X.
2. Arrange for IBC tokens (or any non-EVM-native denom) to be credited to X (e.g., via an IBC transfer, a CosmWasm bridge, or a bank precompile `mint` call).
3. Self-destruct the contract in the same transaction (EIP-6780 permits same-tx destruction). The EVM denom is burned; IBC tokens remain.
4. Redeploy a malicious contract at X using the same CREATE2 parameters.
5. Call a bank precompile or `ExecuteNativeAction` from the new contract to transfer the preserved IBC tokens to the attacker.

This constitutes **unauthorized theft of Cosmos bank funds** through Ethermint stateDB commit logic — matching the Critical/High impact tier: *"Unauthorized theft … of … Cosmos bank funds through Ethermint transaction execution or stateDB/native action logic."*

---

### Likelihood Explanation

- **Attacker-controlled entry path:** Any unprivileged user can deploy a contract via CREATE2 with a chosen salt; no special privilege is required.
- **Token delivery:** IBC transfers to contract addresses are standard on Cosmos chains; CosmWasm bridge contracts routinely credit EVM addresses with non-native denoms.
- **Access mechanism:** Chains that register a bank precompile (as documented in `docs/precompile_creation_guide.md`) or expose `ExecuteNativeAction` give the redeployed contract a direct drain path.
- **Constraint:** The attacker must be the party that originally deployed the contract and arranged for the non-EVM-native tokens to arrive. This limits the attack to self-targeted scenarios unless a CREATE2 address collision with a legitimate protocol is found (the meet-in-the-middle technique from the external report applies equally here).

Likelihood is **Medium** for chains with IBC + bank precompiles, which is the common Ethermint deployment profile.

---

### Recommendation

Extend the post-selfdestruct burn to cover **all** bank denominations held by the destroyed address, not only the EVM denom:

```go
// Burn ALL bank balances, not just the EVM denom.
allBalances := s.keeper.GetAllBalances(cacheCtx, cosmosAddr)
for _, coin := range allBalances {
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrapf(err, "failed to burn post-selfdestruct balance for denom %s", coin.Denom)
    }
}
```

Alternatively, if burning arbitrary IBC denoms is undesirable (e.g., send-disabled coins), the implementation should at minimum document that non-EVM-native tokens at a self-destructed address are **recoverable** via CREATE2 redeployment, and protocols should be warned not to hold such tokens in contracts that can be self-destructed.

---

### Proof of Concept

1. Deploy `SelfDestructExploitFactory` (already in the test suite).
2. Before calling `attackInOneTx`, use a bank precompile or `ExecuteNativeAction` to credit the predicted child address with an IBC denom (e.g., `ibc/...`).
3. Call `attackInOneTx(salt)` — child is deployed, self-destructed, and ETH is forwarded. EVM denom is burned at commit; IBC balance remains.
4. Call `redeployChild(salt)` — child is redeployed at the same address.
5. From the redeployed child, call the bank precompile's `transfer` method to move the IBC tokens to the attacker.
6. Observe that `eth_getBalance` is 0 (EVM denom correctly burned) but the IBC token balance has been transferred to the attacker. [6](#0-5) [7](#0-6)

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

**File:** tests/integration_tests/test_selfdestruct.py (L81-122)
```python
def test_selfdestruct_recreated_address_cannot_recover_funds(ethermint, geth):
    """
    Recreating the child at the same CREATE2 address must not expose any
    preserved balance to the new contract.
    """
    salt = bytes(31) + b"\x02"
    value = 10**9

    def process(w3):
        factory, child_addr, _ = _run(w3, salt, value)
        assert w3.eth.get_balance(child_addr) == 0

        validator_balance_before = w3.eth.get_balance(ADDRS["validator"])

        redeploy_receipt = send_transaction(
            w3,
            factory.functions.redeployChild(salt).build_transaction(
                {"from": ADDRS["validator"]}
            ),
            KEYS["validator"],
        )
        assert redeploy_receipt.status == 1

        return {
            "child_balance_after_redeploy": w3.eth.get_balance(child_addr),
            "validator_gained": w3.eth.get_balance(ADDRS["validator"])
            > validator_balance_before,
            "child_addr": child_addr,
        }

    with ThreadPoolExecutor(2) as pool:
        futs = [pool.submit(process, w3) for w3 in [ethermint.w3, geth.w3]]
        results = {name: f.result() for name, f in zip(["ethermint", "geth"], futs)}

    for name, r in results.items():
        assert r["child_balance_after_redeploy"] == 0, (
            f"{name}: redeployed child must have 0 balance"
            f"(child={r['child_addr']})."
        )
        assert not r[
            "validator_gained"
        ], f"{name}: validator must not gain funds from the recovery attempt."
```

**File:** tests/integration_tests/hardhat/contracts/SelfDestructExploit.sol (L54-78)
```text
    function attackInOneTx(bytes32 salt) external payable returns (address childAddr) {
        bytes memory initCode = targetInitCode;
        assembly {
            childAddr := create2(0, add(initCode, 0x20), mload(initCode), salt)
        }
        require(childAddr != address(0), "Create2 deployment failed");

        SelfDestructTarget(payable(childAddr)).destroy();

        (bool ok, ) = childAddr.call{value: msg.value}("");
        require(ok, "Post-selfdestruct value transfer failed");
    }

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
