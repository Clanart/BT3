# Q2540: EVM OmniBridge native/bridged branching one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `public EVM init/finalize entrypoints` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` violate `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes` in the `one inbound event spawns multiple outbound obligations` attack class because selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
