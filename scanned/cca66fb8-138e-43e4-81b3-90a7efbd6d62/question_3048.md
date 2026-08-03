# Q3048: Decimal Conversion Drift With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `convert_to_erc20` convert between local decimals and remote decimals in a way that lets users receive or refund the wrong amount so `the local amount minted, burned, or released` becomes inconsistent with `the exact economic value implied by the remote amount and configured precisions`, breaking the invariant that precision conversion must preserve economic value across send, receive, and timeout without creating or destroying bridged value and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/impls.rs::convert_to_erc20
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Convert between local decimals and remote decimals in a way that lets users receive or refund the wrong amount. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: precision conversion must preserve economic value across send, receive, and timeout without creating or destroying bridged value
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Fuzz remote and local decimal pairs and assert send, receive, and refund round-trip to one conserved value envelope. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
