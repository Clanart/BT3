# Q3018: Asset-Contract Mapping Misbinding With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept` with attacker-controlled send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `HftError` resolve an incoming token message against the wrong local asset id or wrong remote contract mapping so `the local asset chosen for settlement` becomes inconsistent with `the exact asset configured for that remote chain and remote token contract`, breaking the invariant that remote-token-to-local-asset mappings must bind one remote contract on one chain to one local asset only and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/hyper-fungible-token/src/error.rs::HftError
- Entrypoint: pallet_ismp::handle_unsigned(origin=None, messages) -> pallet_hyper_fungible_token::on_accept
- Attacker controls: send parameters, remote token mappings, precision values, authenticated inbound messages, and timeout inputs
- Exploit idea: Resolve an incoming token message against the wrong local asset id or wrong remote contract mapping. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: remote-token-to-local-asset mappings must bind one remote contract on one chain to one local asset only
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Register adjacent mappings and assert inbound settlement cannot mint or release under the wrong local asset id. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
