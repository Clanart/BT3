# Q1283: EVM OmniBridge native/bridged branching native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public EVM init/finalize entrypoints` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` violate `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes` in the `native versus wrapped branch switch` attack class because selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
