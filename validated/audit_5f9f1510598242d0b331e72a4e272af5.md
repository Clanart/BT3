### Title
Unrestricted `superApprove` in `HyperFungibleTokenImpl` allows any attacker to grant themselves unlimited allowance and drain any holder's balance - (File: `evm/src/utils/HyperFungibleTokenImpl.sol`)

### Summary
`HyperFungibleTokenImpl.superApprove(owner, spender)` is a public function with no access control (`onlyRole`, `msg.sender == owner`, signature, or any other guard) that directly calls the internal `_approve(owner, spender, type(uint256).max)`. Anyone can call it with an arbitrary `owner` and `spender` (e.g., `owner = victim`, `spender = attacker`) to grant an unlimited ERC20 allowance without the token owner's consent, then drain the owner's balance via repeated `transferFrom` calls.

### Finding Description
`HyperFungibleTokenImpl` is deployed as a real, in-scope production contract — it is used as the fee token / cross-chain fungible token in the deployment script (`evm/script/DeployIsmp.s.sol` non-mainnet path deploys `HyperFungibleTokenImpl` as `feeTokenInstance` and wires it into `EvmHost` as `feeToken`), and it is the base contract extended by `FeeToken` (`evm/tests/foundry/FeeToken.sol`) used across the Foundry test suite as the bridge's fee/custody token. [1](#0-0) 

This function bypasses the entire ERC20 `approve`/`transferFrom` consent model: normally only the token owner (via `msg.sender`) can set an allowance for a spender. Here, `superApprove` takes `owner` as an arbitrary parameter with zero authentication, so any unprivileged caller can set `allowance[owner][attacker] = type(uint256).max` for any `owner` they choose, then call the inherited OpenZeppelin `transferFrom(owner, attacker, amount)` repeatedly (down to the owner's balance) with no further consent from the owner. [2](#0-1) 

While the function's docstring claims it is a "Helper function for tests," it lives in `evm/src/utils/HyperFungibleTokenImpl.sol` (production `src/` tree, not `tests/`), is compiled into the production contract, and is deployed on-chain wherever `HyperFungibleTokenImpl` (or `FeeToken`, which inherits it unchanged) is deployed — including as the fee token wired into `EvmHost` via `DeployIsmp.s.sol`. [3](#0-2) 

### Impact Explanation
Any holder of tokens minted by this contract (e.g., the bridge fee token, faucet-distributed tokens) can have their entire balance drained by any unprivileged attacker, with no signature, role, or on-chain consent from the victim required. This directly breaks the "allowance is a one-time, owner-authorized custody permission" invariant and results in unauthorized repeated fund extraction — a direct theft-of-funds impact matching the bounty's required impact categories.

### Likelihood Explanation
The likelihood is high: the function is `public`, requires no privileged role, no proof, no signature, and no prior owner action. An attacker only needs to know the address of a token holder and call two functions (`superApprove` then `transferFrom`) — both fully public and unguarded.

### Recommendation
Remove `superApprove` entirely from the production contract `HyperFungibleTokenImpl.sol`. If a similar helper is genuinely needed for testing, it should live only in a test-only contract/mock under `evm/tests/`, never in the production `src/` tree, and must never be inherited by any contract deployed to a live network (e.g., `FeeToken`).

### Proof of Concept
```solidity
// Foundry test sketch
function testSuperApproveDrain() public {
    // victim holds tokens (e.g., minted via faucet or by admin)
    address victim = makeAddr("victim");
    address attacker = makeAddr("attacker");
    hyperFungibleTokenImpl.mint(victim, 1000e18); // via a legitimate minter role, simulating a real holder

    // attacker, with NO role and NO consent from victim, calls superApprove directly
    vm.prank(attacker);
    hyperFungibleTokenImpl.superApprove(victim, attacker);

    // allowance is now unlimited without victim's signature
    assertEq(hyperFungibleTokenImpl.allowance(victim, attacker), type(uint256).max);

    // attacker repeatedly drains victim's balance
    vm.startPrank(attacker);
    hyperFungibleTokenImpl.transferFrom(victim, attacker, 500e18);
    hyperFungibleTokenImpl.transferFrom(victim, attacker, 500e18);
    vm.stopPrank();

    assertEq(hyperFungibleTokenImpl.balanceOf(victim), 0);
    assertEq(hyperFungibleTokenImpl.balanceOf(attacker), 1000e18);
}
```
This confirms the attacker succeeds without any signature, role check, or receipt from `victim`.

### Citations

**File:** evm/src/utils/HyperFungibleTokenImpl.sol (L26-42)
```text
contract HyperFungibleTokenImpl is ERC20, AccessControlEnumerable {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER ROLE");
    bytes32 public constant BURNER_ROLE = keccak256("BURNER ROLE");

    /// @notice Custom error thrown when a non-gateway address attempts to mint or burn
    error OnlyGateway();

    /**
     * @notice Initializes the token with a name, symbol, and admin
     * @param admin The address that will have DEFAULT_ADMIN_ROLE to grant/revoke roles
     * @param name The name of the token
     * @param symbol The symbol of the token
     */
    constructor(address admin, string memory name, string memory symbol) ERC20(name, symbol) {
        require(admin != address(0), "Admin cannot be zero address");
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }
```

**File:** evm/src/utils/HyperFungibleTokenImpl.sol (L110-117)
```text
    /**
     * @notice Helper function for tests - approves unlimited tokens
     * @param owner The owner of the tokens
     * @param spender The spender address
     */
    function superApprove(address owner, address spender) public {
        _approve(owner, spender, type(uint256).max);
    }
```

**File:** evm/script/DeployIsmp.s.sol (L100-107)
```text
            // Deploy our own feetoken contract & faucet
            faucet = new TokenFaucet{salt: salt}();
            feeTokenInstance = new HyperFungibleTokenImpl{salt: salt}(admin, "Hyper USD", "USD.h");
            // Grant minter role to faucet so it can mint tokens
            feeTokenInstance.grantMinterRole(address(faucet));
            feeToken = address(feeTokenInstance);
            hyperbridge = StateMachine.kusama(paraId);
            decimals = 18;
```
