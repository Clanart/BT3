### Title
ERC20 precompile `transferFrom` always decrements infinite (`type(uint256).max`) allowances, permanently bricking integrations that rely on standard infinite-approval semantics - (File: `precompiles/erc20/tx.go`)

### Summary
The `x/erc20` ERC20 precompile's `transfer` function (used for both `transfer` and `transferFrom`) unconditionally subtracts the transferred amount from the stored allowance whenever `transferFrom` is invoked, with no special-case for `type(uint256).max` ("infinite approval"), unlike OpenZeppelin/Solmate ERC20 implementations that skip the allowance decrement when the allowance equals `type(uint256).max`.

### Finding Description
In <cite repo="bsaldua/push-chain-evm--015" path="precompiles/erc20/tx.go" start="85-109" /> the `transfer` helper, when called through `TransferFrom`, always computes:
```go
newAllowance = new(big.Int).Sub(prevAllowance, amount)
```
and then persists `newAllowance` (or deletes the allowance entry if it becomes zero) via `p.erc20Keeper.SetAllowance`/`DeleteAllowance`, with no branch that checks `prevAllowance.Cmp(abi.MaxUint256) == 0` to skip the decrement. This directly mirrors the reported EBTCToken pattern.

This differs from the reference ERC20 implementations bundled in the same repo, e.g. `_spendAllowance` in <cite repo="bsaldua/push-chain-evm--015" path="contracts/solidity/precompiles/erc20/testdata/ERC20NoMetadata.sol" start="320-335" /> and the OpenZeppelin flattened contract at <cite repo="bsaldua/push-chain-evm--015" path="tests/evm-tools-compatibility/hardhat/Flattened.sol" start="714-720" />, both of which explicitly skip the allowance update `if (currentAllowance != type(uint256).max)`.

The underlying `SetAllowance`/`GetAllowance` keeper logic in <cite repo="bsaldua/push-chain-evm--015" path="x/erc20/keeper/allowance.go" start="76-130" /> has no special-casing for the max value either — it stores whatever `*big.Int` value is passed, up to `256` bits (`value.BitLen() > 256` check at line 113), so an owner setting an allowance of exactly `type(uint256).max` via `Approve` (see `precompiles/erc20/approve.go`) is silently treated the same as any finite allowance.

The precompile's own integration test explicitly acknowledges the resulting semantic gap:
```
// Check that the allowance was removed since we approved only the transferred amount
// FIXME: This is not working for the case where we transfer from the own account
// because the allowance is not removed on the SDK side.
```
<cite repo="bsaldua/push-chain-evm--015" path="tests/integration/precompiles/erc20/test_integration.go" start="827-833" />, and a Solidity JS test at <cite repo="bsaldua/push-chain-evm--015" path="tests/solidity/suites/precompiles/test/3_erc20/erc20.js" start="106-107" /> asserts that the allowance **always** decreases by the transferred amount, confirming there is no infinite-allowance carve-out anywhere in the precompile's production path.

### Impact Explanation
Any smart contract (deployed on the Cosmos EVM chain) that follows the extremely common convention of approving `type(uint256).max` once to a spender contract expecting to use `transferFrom` indefinitely (e.g., router/vault/leverage contracts that never re-approve) will have its allowance silently ground down on every `transferFrom` call routed through the native ERC20 precompile. Once the allowance is exhausted, further `transferFrom` calls revert with `ErrInsufficientAllowance`, permanently freezing/locking the owner's ability to have the spender contract move their precompile-backed (native-coin-backed) tokens, without any error or warning at approval time. This is a "permanent freezing/locking of user funds" scenario matching the allowed Critical impact class, specific to precompile-mediated (bank-module-backed) assets rather than a generic Solidity ERC20 contract issue — it affects the native ERC20 precompile that all native-coin/ERC20 conversions rely on (`x/erc20` TokenPair infrastructure).

### Likelihood Explanation
This requires no privileged access — any user can `approve(spender, type(uint256).max)` on the precompile and then use any downstream contract that assumes standard infinite-allowance semantics; the degradation triggers automatically and deterministically on ordinary `transferFrom` usage, so likelihood of unintentional/eventual freezing is high for any integration built to the common ERC20 convention.

### Recommendation
In `precompiles/erc20/tx.go`'s `transfer` function, add a check before computing `newAllowance`: if `prevAllowance.Cmp(abi.MaxUint256) == 0`, skip the `Sub`/`SetAllowance`/`DeleteAllowance` calls entirely (and skip emitting a changed `Approval` event, or emit the unchanged max value), matching OpenZeppelin/Solmate semantics already documented and tested elsewhere in this same repository.

### Proof of Concept
1. Owner calls `Approve(spender, type(uint256).max)` on the ERC20 precompile for a token pair — `setAllowance` stores `2^256-1` (passes the `BitLen() > 256` check) via <cite repo="bsaldua/push-chain-evm--015" path="precompiles/erc20/approve.go" start="42-59" />.
2. Spender calls `TransferFrom(owner, receiver, amount)` repeatedly through <cite repo="bsaldua/push-chain-evm--015" path="precompiles/erc20/tx.go" start="46-61" />.
3. Each call executes `newAllowance = prevAllowance - amount` and persists it via `SetAllowance`/`DeleteAllowance` <cite repo="bsaldua/push-chain-evm--015" path="precompiles/erc20/tx.go" start="89-109" />, steadily consuming the "infinite" allowance.
4. After enough cumulative transfers exceed `2^256-1` (or, more realistically, whenever the integrating contract expected unlimited future spending without any means to re-approve), the allowance reaches zero and is deleted, and subsequent `TransferFrom` calls revert with `ErrInsufficientAllowance`, permanently freezing the spender contract's ability to move the owner's tokens.