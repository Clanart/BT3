Based on this investigation, there is no valid Protobuf analog to CVE-2020-10574.

The Janus CVE is a specific application-layer bug: an Admin API handler referenced a JSON key string with a typo (a hardcoded name mismatch in `janus.c`'s handler for `query_logger`), causing a null/missing-value dereference or logic failure when that specific admin request was processed. The failed invariant is "a hand-written string literal must match the schema's expected key," and the impact stems from a privileged Admin API being reachable with attacker input that trips a broken string comparison in bespoke request-dispatch code — not a general parser/deserialization framework flaw.

Protobuf's JSON field-name resolution paths that are the closest structural analog are systematically different and not vulnerable to the same class of failure:

- Python's `_ConvertFieldValuePair` builds `fields_by_json_name` directly from the message descriptor (`fields_by_json_name = dict((f.json_name, f) for f in message_descriptor.fields)`) rather than from a hardcoded/typo-able string, and falls through cleanly to a `ParseError` (`Message type "{0}" has no field named...`) when no field matches. [1](#0-0) 
- The C++ upb/descriptor-based JSON parser (`ParseField`) resolves names via `Traits::FieldByName(desc, name)`, driven by the compiled descriptor rather than any typo-prone literal, and explicitly returns `lex.Invalid("no such field: ...")` when lookup fails (or skips the value if `ignore_unknown_fields` is set). [2](#0-1) 
- Java's `JsonFormat.mergeMessage` similarly derives `fieldNameMap` from the descriptor's `getName()`/`getJsonName()`, and throws `InvalidProtocolBufferException` on an unmatched key rather than silently using a wrong/nonexistent name. <invoke name="codebase_search">
</invoke>

### Citations

**File:** python/google/protobuf/json_format.py (L602-662)
```python
    names = []
    message_descriptor = message.DESCRIPTOR
    fields_by_json_name = dict(
        (f.json_name, f) for f in message_descriptor.fields
    )

    def _ClearFieldOrExtension(message, field):
      if field.is_extension:
        message.ClearExtension(field)
      else:
        message.ClearField(field.name)

    def _GetFieldOrExtension(message, field):
      if field.is_extension:
        return message.Extensions[field]
      else:
        return getattr(message, field.name)

    def _SetFieldOrExtension(message, field, value):
      if field.is_extension:
        message.Extensions[field] = value
      else:
        setattr(message, field.name, value)

    for name in js:
      try:
        field = fields_by_json_name.get(name, None)
        if not field:
          field = message_descriptor.fields_by_name.get(name, None)
        if not field and _VALID_EXTENSION_NAME.match(name):
          if not message_descriptor.is_extendable:
            raise ParseError(
                'Message type {0} does not have extensions at {1}'.format(
                    message_descriptor.full_name, path
                )
            )
          identifier = name[1:-1]  # strip [] brackets
          # pylint: disable=protected-access
          field = message.Extensions._FindExtensionByName(identifier)
          # pylint: enable=protected-access
          if not field:
            # Try looking for extension by the message type name, dropping the
            # field name following the final . separator in full_name.
            identifier = '.'.join(identifier.split('.')[:-1])
            # pylint: disable=protected-access
            field = message.Extensions._FindExtensionByName(identifier)
            # pylint: enable=protected-access
        if not field:
          if self.ignore_unknown_fields:
            continue
          raise ParseError(
              (
                  'Message type "{0}" has no field named "{1}" at "{2}".\n'
                  ' Available Fields(except extensions): "{3}"'
              ).format(
                  message_descriptor.full_name,
                  name,
                  path,
                  [f.json_name for f in message_descriptor.fields],
              )
          )
```

**File:** src/google/protobuf/json/internal/parser.cc (L1230-1239)
```text
  } else {
    field = Traits::FieldByName(desc, name);
  }

  if (!field.has_value()) {
    if (!lex.options().ignore_unknown_fields) {
      return lex.Invalid(absl::StrFormat("no such field: '%s'", name));
    }
    return lex.SkipValue();
  }
```
