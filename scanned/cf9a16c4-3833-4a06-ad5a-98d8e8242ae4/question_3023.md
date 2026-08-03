# Q3023: Decimal Conversion Drift After Partial State Change

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and replaying the same public flow after one part of storage changed and another part did not, and make `HftError` convert between local decimals and remote decimals in a way that lets users receive or refund the wrong amount so `the local amount minted, burned, or released` becomes inconsistent with `the exact economic value implied by the remote amount and configured precisions`, breaking the invariant that precision conversion must preserve economic value across send, receive, and timeout without creating or destroying bridged value and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/error.rs::HftError
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Convert between local decimals and remote decimals in a way that lets users receive or refund the wrong amount. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: precision conversion must preserve economic value across send, receive, and timeout without creating or destroying bridged value
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Fuzz remote and local decimal pairs and assert send, receive, and refund round-trip to one conserved value envelope. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
